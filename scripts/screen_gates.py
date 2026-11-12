#!/usr/bin/env python3
"""Screen a candidate version against the two gates BEFORE attempting a bump.

Gate 1 - minCompileSdk: the AAR's META-INF/.../aar-metadata.properties must not demand a
compileSdk above the project's. compileSdk 37 blocked core_ktx, the Compose stack, coil 3.6+
and glide 5.0.9 this way.

Gate 2 - kotlin-stdlib: the artifact's declared kotlin-stdlib must not be on a newer
major.minor line than the project's Kotlin. This is the dangerous one because it fails far
from its cause: coil 3.5.0 declared kotlin-stdlib 2.4.0, passed 2,902 unit tests, and then
broke :app:hiltJavaCompileDebug on device - AGP consistent resolution dragged the whole
classpath onto a 2.4 stdlib whose kotlin.Metadata Hilt could not read. A patch-level
difference on the same line (hilt 2.60.1 declares 2.3.21 against our 2.3.20) is fine.

Usage:
    screen_gates.py <ref>                      # every library on the ref at its latest stable
    screen_gates.py <ref> --version 3.5.0      # a specific version
    screen_gates.py <ref> --bisect             # highest version that passes both gates
    screen_gates.py --coord group:artifact:version
    screen_gates.py --blocked                  # re-screen every blocked tracker row

A failing candidate also prints what it DEPENDS ON (a newer compileSdk, a newer Kotlin), so the
queue can show "X needs Y first" before anything is edited. For `agp` it also checks the
installed Android Studio (studio.py) - Studio caps which AGP the IDE will sync.
"""
from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _catalog  # noqa: E402
import _maven  # noqa: E402
import studio  # noqa: E402


def line(v: str) -> tuple[int, ...]:
    """major.minor of a version: the Kotlin metadata line."""
    return (_maven.nums(v) + (0, 0))[:2]


def screen_coord(group: str, artifact: str, v: str, compile_sdk: int, kotlin: str) -> dict:
    g, a, ver = _maven.android_variant(group, artifact, v)
    shown = f"{g}:{a}:{ver}"
    meta = _maven.aar_metadata(g, a, ver)
    status = meta["status"]
    needs: list[tuple[str, str]] = []
    if status == "ok":
        try:
            mcs = int(str(meta.get("minCompileSdk", "1")).strip())
        except ValueError:
            mcs = None
        if mcs is None:
            g1 = ("UNKNOWN", f"unreadable minCompileSdk {meta.get('minCompileSdk')!r}")
        else:
            g1 = ("PASS", f"minCompileSdk={mcs}") if mcs <= compile_sdk else \
                 ("FAIL", f"minCompileSdk={mcs} > compileSdk {compile_sdk}")
            if mcs > compile_sdk:
                needs.append(("compileSdk", str(mcs)))
    elif status in ("none", "jar"):
        g1 = ("PASS", "no AAR metadata (unconstrained)" if status == "none" else "plain JAR")
    else:
        g1 = ("UNKNOWN", "download failed - NOT the same as 'no metadata'")
    stdlibs, unresolved = _maven.kotlin_stdlib_declared(g, a, ver)
    if stdlibs and line(max(stdlibs, key=_maven.key)) > line(kotlin):
        worst = max(stdlibs, key=_maven.key)
        g2 = ("FAIL", f"kotlin-stdlib {worst} is on a newer line than project Kotlin {kotlin}")
        needs.append(("Kotlin", ".".join(str(n) for n in line(worst))))
    elif unresolved:
        g2 = ("UNKNOWN", "kotlin-stdlib is declared but its version is not readable here (parent POM, "
                         "BOM, range or ${property}) - check the dependency tree by hand")
    elif not stdlibs:
        g2 = ("PASS", "no kotlin-stdlib declared")
    else:
        g2 = ("PASS", f"kotlin-stdlib {max(stdlibs, key=_maven.key)}")
    verdicts = {g1[0], g2[0]}
    verdict = "FAIL" if "FAIL" in verdicts else "UNKNOWN" if "UNKNOWN" in verdicts else "PASS"
    return {"coord": shown, "verdict": verdict, "gate1": g1, "gate2": g2, "needs": needs}


CONCRETE = __import__("re").compile(r"^\d[\w.\-]*$")


def same_group_pulls(group: str, artifact: str, v: str) -> list[tuple[str, str]]:
    """Same-group artifacts this one depends on or constrains, at the versions it pins.

    AndroidX families publish constraints across the family, so moving one member forces the
    rest: navigation 2.10.1 passes on its own but constrains navigation-compose to 2.10.1,
    which needs compileSdk 37. Holding the other ref back does not help. One level deep.
    """
    g, a, ver = _maven.android_variant(group, artifact, v)
    out = []
    for d in _maven.dependencies(g, a, ver):
        dv = d.get("version")
        if d.get("group") == group and d.get("module") not in (artifact, a) and dv \
                and CONCRETE.match(dv):
            out.append((d["module"], dv))
    return out[:25]


def screen_ref_at(cat, ref: str, v: str, compile_sdk: int, kotlin: str) -> dict:
    """Every non-plugin library on `ref` at version `v`, plus what each forces. All must pass."""
    arts = [x for x in cat.artifacts_for(ref) if not x.is_plugin and x.group and x.name]
    results, missing, seen = [], [], set()
    used = {(x.group, x.name) for x in cat.libraries.values() if x.group and x.name}
    for art in arts:
        try:
            _, published = _maven.versions(art.group, art.name)
        except _maven.Unreachable as e:
            results.append({"coord": f"{art.group}:{art.name}:{v}", "verdict": "UNKNOWN",
                            "gate1": ("UNKNOWN", f"repository unreachable: {e}"), "gate2": ("UNKNOWN", "-")})
            continue
        if v not in published:
            missing.append(f"{art.group}:{art.name}")
            continue
        if _maven.packaging(art.group, art.name, v) == "pom":
            # A BOM: screen the members it pins that this catalog actually uses.
            members = [m for m in _maven.bom_managed(art.group, art.name, v) if (m[0], m[1]) in used]
            if not members:
                results.append({"coord": f"{art.group}:{art.name}:{v} (BOM)", "verdict": "UNKNOWN",
                                "gate1": ("UNKNOWN", "BOM pins nothing this catalog uses"),
                                "gate2": ("UNKNOWN", "-")})
            for mg, ma, mv in members:
                r = screen_coord(mg, ma, mv, compile_sdk, kotlin)
                r["coord"] += f"  (pinned by {art.name} {v})"
                results.append(r)
            continue
        results.append(screen_coord(art.group, art.name, v, compile_sdk, kotlin))
        seen.add((art.name, v))
        for mod, dv in same_group_pulls(art.group, art.name, v):
            if (mod, dv) in seen:
                continue
            seen.add((mod, dv))
            r = screen_coord(art.group, mod, dv, compile_sdk, kotlin)
            if r["verdict"] == "FAIL":  # only a failing pulled-in artifact is worth reporting
                r["coord"] += "  (pulled in by constraint)"
                results.append(r)
    if missing:
        verdict = "DIVERGED"
    elif any(r["verdict"] == "FAIL" for r in results):
        verdict = "FAIL"
    elif any(r["verdict"] == "UNKNOWN" for r in results):
        verdict = "UNKNOWN"
    else:
        verdict = "PASS"
    return {"version": v, "verdict": verdict, "results": results, "missing": missing}


def published_or_exit(art) -> list[str]:
    """Published versions, or exit 3: 'repository unreachable' must never read as 'already latest'."""
    try:
        _, published = _maven.versions(art.group, art.name)
    except _maven.Unreachable as e:
        raise SystemExit(f"UNKNOWN: {e} - no conclusion about newer versions; retry when online")
    if not published:
        raise SystemExit(f"UNKNOWN: no maven-metadata.xml found for {art.group}:{art.name} in any repository")
    return published


def primary(cat, ref: str):
    arts = [x for x in cat.artifacts_for(ref) if not x.is_plugin and x.group and x.name]
    if not arts:
        raise SystemExit(f"'{ref}' has no library artifacts in the catalog to screen")
    return arts[0]


def dependencies_of(s: dict) -> list[str]:
    """What a failing screen DEPENDS ON: the other upgrade that must land first.

    One entry per kind, at the highest value any failing artifact asks for, naming who asks.
    compileSdk and Kotlin belong to the platform (Part 1), so the fix is a separate, approved
    step, not a quiet edit alongside this lib.
    """
    best: dict[str, tuple[int, ...]] = {}
    who: dict[str, list[str]] = {}
    for r in s["results"]:
        for kind, val in r.get("needs", []):
            k = tuple(int(x) for x in val.split("."))
            if kind not in best or k > best[kind]:
                best[kind] = k
            parts = r["coord"].split("  (")[0].split(":")
            who.setdefault(kind, []).append(parts[-2] if len(parts) >= 2 else r["coord"])
    out = []
    for kind, k in best.items():
        uniq = list(dict.fromkeys(who[kind]))
        names = ", ".join(uniq[:3]) + (f" +{len(uniq) - 3} more" if len(uniq) > 3 else "")
        out.append(f"{kind} {'.'.join(str(n) for n in k)} (needed by {names})")
    return out


def print_screen(s: dict) -> None:
    print(f"  {s['version']:<18} {s['verdict']}")
    for r in s["results"]:
        print(f"      {r['coord']}")
        print(f"          gate1 {r['gate1'][0]:<7} {r['gate1'][1]}")
        print(f"          gate2 {r['gate2'][0]:<7} {r['gate2'][1]}")
    for m in s["missing"]:
        print(f"      {m}: NOT PUBLISHED at {s['version']} - the ref's artifacts have diverged")
    if s["verdict"] == "FAIL":
        deps = dependencies_of(s)
        if deps:
            print(f"      depends on: {'; '.join(deps)}")


def print_studio(ref: str, version: str) -> None:
    """For the AGP ref only: does the installed Android Studio sync `version`?"""
    if _catalog.TOOLCHAIN_KEYS.get(ref) != "agp":
        return
    found = studio.detect()
    if not found:
        print("=> Android Studio: not detected here - ask the user for their version "
              "(studio.py --studio <version> --agp " + version + ")")
        return
    code, msg = studio.check_agp(found[0]["key"], version)
    tag = "OK" if code == 0 else "DEPENDS ON" if code == 1 else "CHECK"
    print(f"=> Android Studio {found[0]['version']}: {tag} - {msg}")
    print(f"=> lint pairs with AGP {version} at {studio.lint_for(version)}")


def bisect(cat, ref: str, compile_sdk: int, kotlin: str, quiet: bool = False) -> str | None:
    art = primary(cat, ref)
    current = cat.versions[ref].value
    published = published_or_exit(art)
    cands = _maven.stable_candidates(published, current)
    if not cands:
        if not quiet:
            print(f"{ref}: {current} is already the latest stable")
        return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        screens = list(pool.map(lambda v: screen_ref_at(cat, ref, v, compile_sdk, kotlin),
                                reversed(cands)))
    best = next((s["version"] for s in screens if s["verdict"] == "PASS"), None)
    if not quiet:
        print(f"{ref}: current {current}, {len(cands)} stable candidate(s), newest first")
        for s in screens:
            print_screen(s)
        print(f"=> highest version passing both gates: {best or f'none - {current} is the ceiling'}")
        if not screens:  # unreachable while cands is non-empty; fail loudly if that ever changes
            print("error: no candidate was screened", file=sys.stderr)
            sys.exit(1)
        newest = screens[0]
        if newest["verdict"] == "FAIL" and newest["version"] != best:
            deps = dependencies_of(newest)
            if deps:
                print(f"=> newest {newest['version']} depends on: {'; '.join(deps)}")
        print_studio(ref, best or newest["version"])
    return best


def cmd_blocked(cat, compile_sdk: int, kotlin: str, xlsx: str | None) -> int:
    import tracker
    path = tracker.xlsx_path(xlsx)
    blocked = [row for _, row in tracker.rows_all(path) if row.get("status") == "blocked"]
    if not blocked:
        print("no blocked rows")
        return 0
    import re
    gate_words = re.compile(r"compileSdk|minCompileSdk|kotlin-stdlib|\bKOTLIN\b", re.IGNORECASE)
    buckets: dict[str, list[str]] = {"CLEARED": [], "PARTIAL": [], "NOT A GATE": [], "STILL BLOCKED": []}
    for row in blocked:
        ref = row["lib name"]
        if ref not in cat.versions or not cat.artifacts_for(ref):
            print(f"  {ref:<30} SKIP  not a screenable catalog ref")
            continue
        reason = f"{row.get('code changes') or ''} {row.get('build result') or ''}"
        target = str(row.get("new version") or "")
        try:
            best = bisect(cat, ref, compile_sdk, kotlin, quiet=True)
        except SystemExit as e:
            print(f"  {ref:<30} UNKNOWN  {e}")
            continue
        current = cat.versions[ref].value
        if best is None:
            bucket, msg = "STILL BLOCKED", f"no stable version above {current} passes both gates"
        elif not gate_words.search(reason):
            bucket, msg = "NOT A GATE", (f"gates pass up to {best}, but the recorded blocker is not a "
                                         f"gate - it stays blocked until that reason is resolved")
        elif best == target:
            bucket, msg = "CLEARED", f"target {target} now passes both gates - the blocker has cleared"
        else:
            bucket, msg = "PARTIAL", (f"target {target} still fails, but {best} passes: highest-that-works "
                                      f"upgrade available ({current} -> {best})")
        buckets[bucket].append(ref)
        print(f"  {bucket:<13} {ref:<28} {msg}")
        if bucket in ("NOT A GATE", "PARTIAL", "CLEARED"):
            print(f"  {'':<13} {'':<28} recorded: {reason.strip()[:150]}")
    print()
    for bucket, refs in buckets.items():
        if refs:
            print(f"{bucket}: {len(refs)} - {', '.join(refs)}")
    return 1 if buckets["CLEARED"] or buckets["PARTIAL"] else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ref", nargs="?")
    ap.add_argument("--version")
    ap.add_argument("--bisect", action="store_true")
    ap.add_argument("--coord")
    ap.add_argument("--blocked", action="store_true")
    ap.add_argument("--xlsx")
    a = ap.parse_args()

    cat = _catalog.load()
    plat = cat.toolchain()
    missing = [d for d in ("compileSdk", "kotlin") if d not in plat]
    if missing:
        raise SystemExit(f"cannot read {', '.join(missing)} from the project - run preflight.py, or map it in "
                         f"lib-upgrade.config.json under \"toolchainKeys\"")
    try:
        compile_sdk, kotlin = int(plat["compileSdk"]), plat["kotlin"]
    except ValueError:
        raise SystemExit(f"compileSdk {plat['compileSdk']!r} is not a whole API level")
    print(f"project: compileSdk {compile_sdk}, Kotlin {kotlin}\n")

    if a.blocked:
        sys.exit(cmd_blocked(cat, compile_sdk, kotlin, a.xlsx))
    if a.coord:
        parts = a.coord.split(":")
        if len(parts) != 3 or not all(parts):
            ap.error("--coord must be group:artifact:version")
        g, art, v = parts
        r = screen_coord(g, art, v, compile_sdk, kotlin)
        print_screen({"version": v, "verdict": r["verdict"], "results": [r], "missing": []})
        sys.exit(0 if r["verdict"] == "PASS" else 1)
    if not a.ref:
        ap.error("give a ref, --coord or --blocked")
    if a.ref not in cat.versions:
        raise SystemExit(f"'{a.ref}' is not a [versions] key in the catalog")
    if a.bisect:
        sys.exit(0 if bisect(cat, a.ref, compile_sdk, kotlin) else 1)
    art = primary(cat, a.ref)
    v = a.version
    if not v:
        published = published_or_exit(art)
        cands = _maven.stable_candidates(published, cat.versions[a.ref].value)
        if not cands:
            print(f"{a.ref}: already at the latest stable ({cat.versions[a.ref].value})")
            sys.exit(0)
        v = cands[-1]
    s = screen_ref_at(cat, a.ref, v, compile_sdk, kotlin)
    print(f"{a.ref} @ {v}:")
    print_screen(s)
    print_studio(a.ref, v)
    sys.exit(0 if s["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
