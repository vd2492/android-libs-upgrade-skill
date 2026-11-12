#!/usr/bin/env python3
"""Inventory the version catalog: every ref's current version against its latest stable.

Latest versions come from each repository's maven-metadata.xml - the complete version list -
never from a search API. (Solr search with rows=40 once returned an arbitrary window and hid
11 outdated libraries, one of which never entered the tracker at all.)

--part sdk   the platform: Gradle, AGP, Kotlin, KSP, compileSdk/targetSdk (Part 1 of the skill)
--part libs  every other ref (Part 2)

A ref drives every library and plugin that names it via version.ref. When those artifacts'
latest versions disagree the ref is DIVERGED and cannot be bumped as one (litert and
litert-support-api once split this way: litert reached 2.x, litert-support-api stopped at 1.4.2).

Usage:
    scan_catalog.py --part libs [--out build/lib-upgrade/scan.json] [--no-modules]
    scan_catalog.py --part sdk
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _catalog  # noqa: E402
import _maven  # noqa: E402

NOT_LIBS = {"versioncode", "versionname"}   # compared after _catalog._norm()
SDK_REPO = "https://dl.google.com/android/repository/repository2-3.xml"


def latest_api_level() -> str | None:
    raw = _maven.fetch(SDK_REPO, timeout=90)
    if not raw:
        return None
    levels = re.findall(r'path="platforms;android-(\d+)(?:\.\d+)?"', raw.decode("utf-8", "replace"))
    return str(max(int(x) for x in levels)) if levels else None


def latest_gradle() -> str | None:
    raw = _maven.fetch("https://services.gradle.org/versions/current")
    try:
        return json.loads(raw)["version"] if raw else None
    except (ValueError, KeyError):
        return None


def coords_for(cat, ref: str) -> list[tuple[str, str]]:
    libs = [(a.group, a.name) for a in cat.artifacts_for(ref) if not a.is_plugin and a.group and a.name]
    if libs:
        return libs
    return [_maven.plugin_marker(a.group) for a in cat.artifacts_for(ref) if a.is_plugin and a.group]


def scan_ref(cat, ref: str) -> dict:
    v = cat.versions[ref]
    entry = {"ref": ref, "current": v.value, "comment": v.comment,
             "toolchain": ref in _catalog.TOOLCHAIN_KEYS}
    coords = coords_for(cat, ref)
    if not coords:
        entry.update(state="no-artifact", note="no catalog library or plugin uses this ref - it may be "
                     "read directly as libs.versions.<ref>; run modules.py consumers " + ref)
        return entry
    per = []
    for g, a in coords:
        repo, published = _maven.versions(g, a)
        cands = _maven.stable_candidates(published, v.value)
        per.append({"coord": f"{g}:{a}", "repo": repo, "latest": cands[-1] if cands else
                    (v.value if published else None)})
    known = [p for p in per if p["latest"]]
    entry["artifacts"] = per
    if not known:
        entry.update(state="unresolved", note="no public maven-metadata.xml found for any artifact - "
                     "check this ref by hand")
        return entry
    latests = sorted({p["latest"] for p in known}, key=_maven.key)
    newest = latests[-1]
    if len(latests) > 1:
        entry.update(state="diverged", latest=newest, tier=_maven.tier(v.value, newest),
                     note="artifacts on this ref disagree: " +
                          ", ".join(f"{p['coord']} -> {p['latest']}" for p in known) +
                          " - it cannot be bumped as one ref")
    elif _maven.key(newest) > _maven.key(v.value):
        entry.update(state="behind", latest=newest, tier=_maven.tier(v.value, newest))
    else:
        entry.update(state="current", latest=newest)
    first = coords[0]
    entry["docs"] = _maven.release_notes_url(first[0], first[1], newest)
    return entry


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", choices=["libs", "sdk", "all"], default="libs")
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-modules", action="store_true", help="skip consumer lookup (faster)")
    a = ap.parse_args()

    cat = _catalog.load()
    refs = [k for k in cat.versions if _catalog._norm(k) not in NOT_LIBS]
    if a.part == "libs":
        refs = [k for k in refs if k not in _catalog.TOOLCHAIN_KEYS]
    elif a.part == "sdk":
        refs = [k for k in refs if k in _catalog.TOOLCHAIN_KEYS]

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda r: scan_ref(cat, r), refs))

    platform = _catalog.platform()
    extra = {}
    if a.part in ("sdk", "all"):
        extra = {"latest_api_level": latest_api_level(), "latest_gradle": latest_gradle()}
        api = extra["latest_api_level"]
        for r in results:
            dim = _catalog.TOOLCHAIN_KEYS.get(r["ref"])
            if dim in ("compileSdk", "targetSdk") and api:
                behind = int(api) > int(r["current"])
                r.update(state="behind" if behind else "current", latest=api, tier="T2",
                         docs=f"https://developer.android.com/about/versions/{api}/behavior-changes-{api}",
                         note=("compileSdk: API availability only, no behaviour change - bump first; it "
                               "unblocks libraries whose AAR metadata demands it" if dim == "compileSdk"
                               else "targetSdk: opts in to behaviour changes - read the behaviour-changes page "
                                    "AND the previous level's, since some changes were already enforced there"))
            elif dim == "minSdk":
                r.update(state="policy", note="minSdk is a product decision, not a version bump - not scanned")

    if not a.no_modules:
        import modules
        for r in results:
            if r["state"] in ("behind", "diverged"):
                hits = modules.consumers(r["ref"])
                r["modules"] = f"{len(hits)} module(s): " + ", ".join(h["path"] for h in hits[:12]) + \
                               (" ..." if len(hits) > 12 else "")

    # Gradle lives in the wrapper, not the catalog; give it a ref so tracker.py init makes a row.
    g_cur, g_new = platform.get("gradle"), extra.get("latest_gradle")
    if g_cur and g_new:
        behind = _maven.key(g_new) > _maven.key(g_cur)
        results.append({"ref": "gradle", "current": g_cur, "toolchain": True, "latest": g_new,
                        "state": "behind" if behind else "current", "tier": _maven.tier(g_cur, g_new),
                        "docs": f"https://docs.gradle.org/{g_new}/release-notes.html",
                        "modules": "all (wrapper)",
                        "note": "bump with ./gradlew wrapper --gradle-version " + g_new})

    out = a.out or os.path.join(_catalog.repo_root(), "build", "lib-upgrade", f"scan-{a.part}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"generated": datetime.date.today().isoformat(), "part": a.part,
                   "platform": platform, **extra, "refs": results}, fh, indent=2)

    order = {"behind": 0, "diverged": 1, "unresolved": 2, "no-artifact": 3, "policy": 4, "current": 5}
    print(f"platform: " + ", ".join(f"{k} {v}" for k, v in sorted(platform.items())))
    if extra:
        print(f"latest:   API level {extra['latest_api_level']}, Gradle {extra['latest_gradle']}")
        if extra["latest_api_level"] and int(extra["latest_api_level"]) > int(platform.get("compileSdk", 0)):
            print(f"          -> compileSdk {platform['compileSdk']} is behind API {extra['latest_api_level']}")
        if extra["latest_gradle"] and _maven.key(extra["latest_gradle"]) > _maven.key(platform.get("gradle", "0")):
            print(f"          -> Gradle {platform['gradle']} is behind {extra['latest_gradle']}")
    print()
    for r in sorted(results, key=lambda r: (order[r["state"]], r.get("tier", ""), r["ref"])):
        if r["state"] == "current":
            continue
        tier = r.get("tier", "")
        latest = r.get("latest") or ""
        print(f"  {r['state']:<11} {tier:<3} {r['ref']:<34} {r['current']:<16} -> {latest}")
        if r.get("note"):
            print(f"  {'':<15} {r['note'][:150]}")
    counts = {s: sum(1 for r in results if r["state"] == s) for s in order}
    print("\n" + " · ".join(f"{n} {s}" for s, n in counts.items() if n) + f"   -> {out}")


if __name__ == "__main__":
    main()
