#!/usr/bin/env python3
"""Compare R8's output for the staging variant before and after the upgrades.

A debug build does not minify, so a bumped lib can pass every unit test and install cleanly
on a debug build, then break in staging or release: R8 strips a class the lib reads by
reflection, or a new transitive class has no keep rule. AGP writes R8's missing-class warnings
to <app module>/build/outputs/mapping/<variant>/missing_rules.txt as `-dontwarn` lines; a NEW line there
after the upgrades is the signal this reads.

A baseline is a snapshot of that file taken BEFORE the first bump, from whatever staging build
already exists - the skill never triggers a build for it. With no baseline, compare lists every
entry and says it could not tell new from old.

Freshness matters as it does for tests: if mapping.txt is not newer than the baseline's, the
staging build did not run (up to date or from cache) and the result says STALE instead of
passing.

Usage:
    r8_check.py snapshot <name> [--variant V]    # save missing_rules.txt as <name>
    r8_check.py compare  <name> [--variant V]    # NEW / gone entries since <name>
Exit 1 on NEW warnings, STALE, or no R8 output; 0 when clean.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _catalog  # noqa: E402

GENERIC = {"com", "org", "net", "io", "android", "androidx", "google", "kotlin", "kotlinx", "java",
           "javax", "core", "lib", "base", "common", "sdk", "api", "impl", "internal", "util",
           "utils", "client", "main", "gradle", "plugin", "library", "annotations", "annotation"}


def app_dir() -> str:
    """The application module's directory: config, then `app/`, then the module applying the app plugin."""
    configured = _catalog.config().get("appModuleDir")
    if configured:
        return configured
    root = _catalog.repo_root()
    if any(os.path.exists(os.path.join(root, "app", f)) for f in ("build.gradle", "build.gradle.kts")):
        return "app"
    try:
        import modules
        for p in modules.projects():
            if p["build"] and re.search(r"com\.android\.application|libs\.plugins\.android\.application",
                                         open(p["build"], encoding="utf-8", errors="ignore").read()):
                return p["dir"]
    except (SystemExit, OSError):
        pass
    return "app"


def default_variant() -> str:
    """The variant to compare: config, else the first of staging/release that has R8 output."""
    configured = _catalog.config().get("r8Variant")
    if configured:
        return configured
    for v in ("staging", "release"):
        if os.path.isdir(mapping_dir(v)):
            return v
    return "release"


def mapping_dir(variant: str) -> str:
    return os.path.join(_catalog.repo_root(), app_dir(), "build", "outputs", "mapping", variant)


def snap_path(name: str) -> str:
    d = os.path.join(_catalog.repo_root(), "build", "lib-upgrade", "r8")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{_catalog.safe_name(name)}.json")


def read_state(variant: str) -> dict:
    d = mapping_dir(variant)
    mapping = os.path.join(d, "mapping.txt")
    missing = os.path.join(d, "missing_rules.txt")
    entries: list[str] = []
    if os.path.exists(missing):
        with open(missing, encoding="utf-8", errors="ignore") as fh:
            entries = sorted({ln.strip() for ln in fh if ln.strip() and not ln.strip().startswith("#")})
    return {"variant": variant, "mapping_mtime": os.path.getmtime(mapping) if os.path.exists(mapping) else None,
            "missing": entries}


def tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) >= 4 and t not in GENERIC}


def bumped_refs() -> list[str]:
    """Refs bumped this run: tracker rows that are awaiting device or done."""
    try:
        import tracker
        path = tracker.xlsx_path(None)
        return [r["lib name"] for part, r in tracker.rows_all(path)
                if part == "libs" and (r.get("status") or "") in ("awaiting device", "done")]
    except (SystemExit, Exception):  # best-effort: a missing/corrupt tracker or no openpyxl must not fail the R8 check
        return []


def attribute(entries: list[str]) -> dict[str, list[str]]:
    """Guess which bumped ref each new -dontwarn entry belongs to, by shared name tokens."""
    cat = _catalog.load()
    ref_tokens = {}
    for ref in bumped_refs():
        if ref not in cat.versions:
            continue
        t = tokens(ref)
        for art in cat.artifacts_for(ref):
            for part in (art.group, art.name):
                if part:  # an empty/None part would otherwise add the token "none"
                    t |= tokens(part)
        ref_tokens[ref] = t
    out: dict[str, list[str]] = {}
    for e in entries:
        cls = e.replace("-dontwarn", "").strip()
        hits = [ref for ref, t in ref_tokens.items() if t & tokens(cls)]
        out[e] = hits
    return out


def cmd_snapshot(a) -> None:
    state = read_state(a.variant)
    state["taken"] = time.time()
    with open(snap_path(a.name), "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)
    if state["mapping_mtime"] is None:
        print(f"snapshot '{a.name}': no R8 output for {a.variant} yet (no mapping.txt) - a baseline of "
              f"nothing; compare will list every entry and cannot tell new from old")
    else:
        print(f"snapshot '{a.name}': {len(state['missing'])} missing-class entr(ies) in {a.variant}")


def cmd_compare(a) -> None:
    cur = read_state(a.variant)
    if cur["mapping_mtime"] is None:
        print(f"NO R8 OUTPUT: {mapping_dir(a.variant)}/mapping.txt does not exist - the {a.variant} build "
              f"did not minify, or has not run. This build proves nothing about R8.")
        sys.exit(1)
    base = None
    try:
        with open(snap_path(a.name), encoding="utf-8") as fh:
            base = json.load(fh)
        if not isinstance(base, dict):
            raise ValueError("not a snapshot object")
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:  # unreadable or corrupt snapshot: say so, then treat as no baseline
        print(f"baseline '{a.name}' is unreadable ({e}) - treating it as missing")
        base = None
    if base and base.get("mapping_mtime") and cur["mapping_mtime"] <= base["mapping_mtime"]:
        print(f"STALE: mapping.txt is not newer than the '{a.name}' baseline - the {a.variant} build did "
              f"not run since. Rebuild it; this proves nothing.")
        sys.exit(1)
    if base is None:
        print(f"no baseline '{a.name}' - listing every entry; cannot tell new from old")
        new, gone = cur["missing"], []
    else:
        base_missing = base.get("missing", [])
        base_set, cur_set = set(base_missing), set(cur["missing"])
        new = [e for e in cur["missing"] if e not in base_set]
        gone = [e for e in base_missing if e not in cur_set]
    if gone:
        print(f"{len(gone)} entr(ies) no longer reported")
    if not new:
        print(f"CLEAN: no new R8 missing-class warnings in {a.variant}")
        sys.exit(0)
    print(f"{'NEW WARNINGS' if base else 'ENTRIES'}: {len(new)} in {a.variant}")
    for e, refs in attribute(new).items():
        print(f"  {e:<80} {'possibly: ' + ', '.join(refs) if refs else 'unattributed'}")
    print("A missing class is a warning, not a crash: check the possibly-named libs first, then "
          "launch the staging build and exercise what they do.")
    sys.exit(1 if base else 0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("snapshot", "compare"):
        p = sub.add_parser(c); p.add_argument("name"); p.add_argument("--variant", default=None, help="build variant (default: lib-upgrade.config.json r8Variant, else staging/release)")
    a = ap.parse_args()
    a.variant = a.variant or default_variant()
    {"snapshot": cmd_snapshot, "compare": cmd_compare}[a.cmd](a)


if __name__ == "__main__":
    main()
