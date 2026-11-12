#!/usr/bin/env python3
"""Diff a library's real public API between two versions, from the shipped bytecode.

For majors, the changelog is not enough. AppsFlyer 6.18 -> 7.0.1 removed 26 AppsFlyerLib
methods; exactly one of them (start(Context)) was called here. MoEngage 14 -> 15 turned
"SDK not initialised" from a silent no-op into a thrown exception across whole classes - a
change no signature list shows, but a `throws` clause does.

An umbrella artifact ships almost nothing (moe-android-sdk's classes.jar is 1.4 KB); --follow
pulls in its same-group dependencies so the classes you actually call are included. Kotlin
Multiplatform roots are resolved to their Android variant.

Usage:
    api_diff.py com.appsflyer:af-android-sdk 6.18.0 7.0.1 --class com.appsflyer.AppsFlyerLib
    api_diff.py com.moengage:moe-android-sdk 14.10.00 15.02.00 --follow \\
        --class com.moengage.inapp.MoEInAppHelper --class com.moengage.core.MoECoreHelper
    api_diff.py com.moengage:moe-android-sdk 14.10.00 15.02.00 --follow --symbols imports.txt
    api_diff.py com.moengage:moe-android-sdk 14.10.00 15.02.00 --deps
"""
from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _catalog  # noqa: E402
import _maven  # noqa: E402

WORK = os.path.join("build", "lib-upgrade", "api")


def classes_jar(group: str, artifact: str, v: str) -> bytes | None:
    g, a, ver = _maven.android_variant(group, artifact, v)
    for ext in ("aar", "jar"):
        for repo in _maven.repos():
            raw = _maven.fetch_verified(_maven.artifact_url(repo, g, a, ver, ext))
            if not raw:
                continue
            if ext == "jar":
                return raw
            try:
                zf = zipfile.ZipFile(io.BytesIO(raw))
                return zf.read("classes.jar") if "classes.jar" in zf.namelist() else None
            except zipfile.BadZipFile:
                print(f"warning: {g}:{a}:{ver} {ext} is not a valid zip - skipped", file=sys.stderr)
                continue
    return None


def extract(group: str, artifact: str, v: str, follow: bool) -> tuple[str, list[str]]:
    """Unpack the artifact (and, with follow, its same-group deps) into one class dir."""
    label = re.sub(r"[^A-Za-z0-9._-]", "_", f"{group}_{artifact}_{v}{'_f' if follow else ''}")
    dest = os.path.join(_catalog.repo_root(), WORK, label)
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest)
    included = []
    todo = [(group, artifact, v)]
    if follow:
        for d in _maven.dependencies(*_maven.android_variant(group, artifact, v)):
            if d.get("group") == group and d.get("version"):
                todo.append((d["group"], d["module"], d["version"]))
    for g, a, ver in todo:
        raw = classes_jar(g, a, ver)
        if raw:
            try:
                zipfile.ZipFile(io.BytesIO(raw)).extractall(dest)   # extractall drops '..' and absolute paths
            except zipfile.BadZipFile:
                print(f"warning: {g}:{a}:{ver} classes are not a valid zip - skipped", file=sys.stderr)
                continue
            included.append(f"{g}:{a}:{ver} ({len(raw) // 1024} KB)")
    return dest, included


def javap(cp: str, cls: str) -> list[str] | None:
    out = subprocess.run(["javap", "-p", "-cp", cp, cls], capture_output=True, text=True)
    if out.returncode != 0:
        return None
    return [re.sub(r"\s+", " ", ln.strip()) for ln in out.stdout.splitlines()
            if ln.strip().startswith(("public", "protected"))]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("coord", help="group:artifact")
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--class", dest="classes", action="append", default=[])
    ap.add_argument("--symbols", help="file of fully-qualified class names that must still exist")
    ap.add_argument("--follow", action="store_true", help="include same-group dependencies")
    ap.add_argument("--deps", action="store_true", help="only list same-group dependencies of each side")
    a = ap.parse_args()
    if not shutil.which("javap") and not a.deps:
        raise SystemExit("javap not found - install a JDK")
    parts = a.coord.split(":")
    if len(parts) != 2 or not all(parts):
        ap.error("coord must be group:artifact")
    group, artifact = parts
    for cls in a.classes:
        if cls.startswith("-"):
            ap.error(f"invalid class name {cls!r}")

    if a.deps:
        for v in (a.old, a.new):
            print(f"{a.coord}:{v} ->")
            for d in _maven.dependencies(*_maven.android_variant(group, artifact, v)):
                if d.get("group") == group:
                    print(f"    {d['group']}:{d['module']}:{d['version']}")
        return

    old_dir, old_inc = extract(group, artifact, a.old, a.follow)
    new_dir, new_inc = extract(group, artifact, a.new, a.follow)
    print(f"old: {'; '.join(old_inc) or 'NOTHING DOWNLOADED'}")
    print(f"new: {'; '.join(new_inc) or 'NOTHING DOWNLOADED'}")
    if old_inc and all(int(re.search(r'\((\d+) KB', x).group(1)) < 4 for x in old_inc) and not a.follow:
        print("  note: the classes here are tiny - this looks like an umbrella artifact; re-run with --follow")

    exit_code = 0
    if a.symbols:
        with open(a.symbols, encoding="utf-8") as fh:
            wanted = [(ln.strip()[7:] if ln.strip().startswith("import ") else ln.strip()).rstrip(";") for ln in fh if ln.strip()]
        print(f"\nsymbols in {a.new}:")
        for s in wanted:
            ok = os.path.exists(os.path.join(new_dir, s.replace(".", "/") + ".class"))
            print(f"  {'OK     ' if ok else 'MISSING'} {s}")
            exit_code |= 0 if ok else 1

    for cls in a.classes:
        before, after = javap(old_dir, cls), javap(new_dir, cls)
        print(f"\n=== {cls} ===")
        if before is None or after is None:
            print(f"  {'absent in ' + a.old if before is None else ''} {'absent in ' + a.new if after is None else ''}")
            exit_code = 1
            continue
        removed = sorted(set(before) - set(after))
        added = sorted(set(after) - set(before))
        def call_shape(sig: str) -> str | None:  # name(params), ignoring modifiers and return type
            m = re.search(r"(\w+\([^)]*\))", sig)
            return m.group(1) if m else None
        unthrowing_before = {call_shape(b) for b in before if "throws" not in b}
        throws_new = [s for s in added if "throws" in s and call_shape(s) in unthrowing_before]
        for s in removed:
            print(f"  - {s}")
        for s in added:
            print(f"  + {s}")
        if throws_new:
            print(f"  ! {len(throws_new)} method(s) kept their name and parameters but gained a throws "
                  f"clause - Java call sites must now catch it (Kotlin ignores it); check whether it is "
                  f"actually thrown at runtime, e.g. behind a flag")
        if not removed and not added:
            print("  identical public API")
        exit_code |= 1 if removed else 0
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
