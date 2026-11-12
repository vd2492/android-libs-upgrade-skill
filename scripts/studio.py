#!/usr/bin/env python3
"""Detect the installed Android Studio and say which AGP it can sync.

Android Studio caps AGP: AGP 9.4.0 built clean from the CLI while a Studio that stopped at 9.2
refused to sync it, and the project sat on AGP 9.2.1 until someone noticed. This reads the
installed Studio instead of asking, and maps it to the highest AGP it supports.

The Studio-to-AGP table below is copied from Google's compatibility table
(developer.android.com/studio/releases, "Android Gradle plugin and Android Studio
compatibility"). It is only as current as TABLE_CHECKED - when it is old, or a version falls
outside it, this says UNKNOWN rather than guessing, and the page is the source of truth.

A Studio install reports only year.minor ("2026.2"), not the patch ("2026.2.2"). Where the
patch changes the answer, the result is a range: sure up to X, possibly Y, confirm in
Android Studio > About.

Usage:
    studio.py                      # detect, and print what each installed Studio supports
    studio.py --studio 2026.2.1    # skip detection, use this version
    studio.py --agp 9.5.1          # exit 0 supported, 1 needs a newer Studio, 2 depends on patch/unknown
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import plistlib
import re
import sys

TABLE_CHECKED = "2026-11-12"

# (year, minor, patch) -> highest AGP (major, minor) that Studio version supports.
TABLE: dict[tuple[int, ...], tuple[int, int]] = {
    (2025, 3, 4): (9, 2),   # Panda 4
    (2026, 1, 1): (9, 2),   # Quail 1
    (2026, 1, 2): (9, 3),   # Quail 2
    (2026, 1, 3): (9, 3),   # Quail 3
    (2026, 1, 4): (9, 4),   # Quail 4
    (2026, 2, 1): (9, 4),   # Rabbit 1
    (2026, 2, 2): (9, 5),   # Rabbit 2
}

APP_GLOBS = [
    "/Applications/Android Studio*.app",
    os.path.expanduser("~/Applications/Android Studio*.app"),
    os.path.expanduser("~/Library/Application Support/JetBrains/Toolbox/apps/*/*/*/Android Studio*.app"),
    os.path.expanduser("~/Library/Application Support/JetBrains/Toolbox/apps/*/*/Android Studio*.app"),
]


def vkey(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", v))[:3]


def fmt(t: tuple[int, ...]) -> str:
    return ".".join(str(p) for p in t)


# Linux and Windows installs have no Info.plist; their product-info.json names the data directory
# ("AndroidStudio2026.2"), which carries the version. Paths are the usual defaults - UNVERIFIED on
# those platforms, so a miss just falls back to asking the user.
PRODUCT_INFO_GLOBS = [
    "/opt/android-studio*/product-info.json",
    "/usr/local/android-studio*/product-info.json",
    os.path.expanduser("~/android-studio*/product-info.json"),
    os.path.expanduser("~/.local/share/JetBrains/Toolbox/apps/*/*/*/product-info.json"),
    "/snap/android-studio/current/android-studio/product-info.json",
    "C:/Program Files/Android/Android Studio*/product-info.json",
    os.path.expandvars("%LOCALAPPDATA%/Programs/Android Studio*/product-info.json"),
    os.path.expandvars("%LOCALAPPDATA%/JetBrains/Toolbox/apps/*/*/*/product-info.json"),
]


def detect_product_info() -> list[dict]:
    found = []
    for pattern in PRODUCT_INFO_GLOBS:
        for path in glob.glob(pattern):
            try:
                with open(path, encoding="utf-8") as fh:
                    info = json.load(fh)
            except (OSError, ValueError):
                continue
            if not isinstance(info, dict) or "android" not in str(info.get("name", "")).lower():
                continue
            m = re.search(r"(\d{4}\.\d+(?:\.\d+)?)", str(info.get("dataDirectoryName", "")))
            if m:
                found.append({"path": os.path.dirname(path), "version": m.group(1),
                              "build": str(info.get("buildNumber", "")), "key": vkey(m.group(1))})
    return found


def detect() -> list[dict]:
    """Every Android Studio install found, newest first. Empty when none is found."""
    found, seen = detect_product_info(), set()
    for pattern in APP_GLOBS:
        for app in glob.glob(pattern):
            real = os.path.realpath(app)
            if real in seen:
                continue
            seen.add(real)
            try:
                with open(os.path.join(app, "Contents", "Info.plist"), "rb") as fh:
                    version = plistlib.load(fh).get("CFBundleShortVersionString", "")
            except (OSError, plistlib.InvalidFileException, AttributeError):
                continue
            if not isinstance(version, str) or not re.match(r"^\d{4}\.\d+", version):
                continue
            build = ""
            try:
                with open(os.path.join(app, "Contents", "Resources", "product-info.json"),
                          encoding="utf-8") as fh:
                    build = json.load(fh).get("buildNumber", "")
            except (OSError, ValueError, AttributeError):
                pass
            found.append({"path": app, "version": version, "build": build, "key": vkey(version)})
    return sorted(found, key=lambda d: d["key"], reverse=True)


def support(studio: tuple[int, ...]) -> dict:
    """What `studio` (year.minor or year.minor.patch) supports: sure / possible AGP, or UNKNOWN."""
    if len(studio) >= 3:
        agp = TABLE.get(studio[:3])
        return {"status": "OK", "sure": agp, "possible": agp} if agp else {"status": "UNKNOWN"}
    same_minor = [v for k, v in TABLE.items() if k[:2] == studio[:2]]
    if not same_minor:
        return {"status": "UNKNOWN"}
    sure, possible = min(same_minor), max(same_minor)
    return {"status": "OK" if sure == possible else "PATCH", "sure": sure, "possible": possible}


def min_studio_for(agp: tuple[int, int]) -> tuple[int, ...] | None:
    """The oldest Studio in the table that supports `agp`, or None if the table stops short."""
    ok = [k for k, v in TABLE.items() if v >= agp]
    return min(ok) if ok else None


def lint_for(agp: str) -> str:
    """lint tracks AGP: major + 23, same minor and patch (AGP 9.5.1 <-> lint 32.5.1)."""
    p = [int(x) for x in re.findall(r"\d+", agp)[:3]]
    if not p:
        return "unknown"
    return ".".join(str(x) for x in [p[0] + 23] + p[1:])


def check_agp(studio: tuple[int, ...], agp: str) -> tuple[int, str]:
    """(exit code, message): 0 supported, 1 needs a newer Studio, 2 depends on the patch / unknown."""
    want = tuple(int(x) for x in re.findall(r"\d+", agp)[:2])
    if len(want) < 2:
        return 2, f"'{agp}' is not an AGP version (expected major.minor, e.g. 9.5.1)"
    s = support(studio)
    if s["status"] == "UNKNOWN":
        return 2, (f"Studio {fmt(studio)} is not in the table (checked {TABLE_CHECKED}) - "
                   f"look up AGP {agp} on developer.android.com/studio/releases")
    if want <= s["sure"]:
        return 0, f"Studio {fmt(studio)} supports AGP {agp}"
    need = min_studio_for(want)
    need_txt = f"Android Studio {fmt(need)} or newer" if need else \
        "a Studio newer than the table knows (check developer.android.com/studio/releases)"
    if want <= s["possible"]:
        return 2, (f"AGP {agp} needs {need_txt}; Studio {fmt(studio)} may be that patch - "
                   f"confirm in Android Studio > About")
    return 1, f"AGP {agp} needs {need_txt}; Studio {fmt(studio)} stops at AGP {s['sure'][0]}.{s['sure'][1]}"


def describe(studio: tuple[int, ...]) -> str:
    s = support(studio)
    if s["status"] == "UNKNOWN":
        return f"Studio {fmt(studio)}: not in the table (checked {TABLE_CHECKED})"
    sure = f"{s['sure'][0]}.{s['sure'][1]}"
    if s["status"] == "OK":
        return f"Studio {fmt(studio)}: supports AGP up to {sure}"
    return (f"Studio {fmt(studio)}: AGP up to {sure} for sure, {s['possible'][0]}.{s['possible'][1]} "
            f"if it is patch {fmt(min(k for k, v in TABLE.items() if k[:2] == studio[:2] and v == s['possible']))}"
            f" or later (confirm in Android Studio > About)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--studio", help="skip detection and use this Studio version, e.g. 2026.2.1")
    ap.add_argument("--agp", help="check this AGP version against the Studio")
    a = ap.parse_args()

    if a.studio:
        studios = [{"path": "(given)", "version": a.studio, "build": "", "key": vkey(a.studio)}]
    else:
        studios = detect()
    if not studios:
        print("no Android Studio found on this machine - ask the user for their version "
              "(--studio <version>)")
        sys.exit(2)
    for s in studios:
        build = f" build {s['build']}" if s["build"] else ""
        print(f"{describe(s['key'])}   [{s['path']}{build}]")
    if len(studios) > 1:
        print(f"{len(studios)} installs found - using the newest ({studios[0]['version']}); "
              "this reads the installed apps, not the one the user opens")
    print(f"(table checked {TABLE_CHECKED}; detection reads only this machine, not the team's)")
    if a.agp:
        code, msg = check_agp(studios[0]["key"], a.agp)
        print(("OK   " if code == 0 else "NEEDS" if code == 1 else "CHECK") + f" {msg}")
        print(f"lint pairs with AGP {a.agp} at {lint_for(a.agp)}")
        sys.exit(code)


if __name__ == "__main__":
    main()
