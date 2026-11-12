#!/usr/bin/env python3
"""Map a catalog ref to the modules that consume it, and each module to its real test task.

Project paths come from settings.gradle, never from directory names: platform/webrtc-call is
:platform:webrtccall, and projects are declared both as `include ':x'` and `include(":x")`
with projectDir overrides. Test tasks come from the build file: a pure JVM module
(:platform:linter) has `test`, not `testDebugUnitTest`, and asking for the wrong one fails the
whole Gradle invocation in seconds.

Consumers are found through every route a catalog entry reaches a module: a direct
`libs.<alias>`, a bundle, a plugin alias, `libs.versions.<ref>`, a convention plugin in
build-logic that looks the entry up by name, and - separately flagged - coordinates hardcoded
outside the catalog (supply/profile pinned moe-android-sdk:13.06.00 that way).

Usage:
    modules.py list                 # every project: path, dir, kind, test task, has tests
    modules.py consumers <ref>      # who uses this ref, and how
    modules.py tasks <ref>          # the Gradle test tasks to run after bumping <ref>
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _catalog  # noqa: E402

# End of a catalog accessor: not a longer name (libs.foo vs libs.foo.bar) but still a match when the
# accessor is followed by a Provider call - libs.versions.compose.get(), libs.x.asProvider() -
# which is how most build files read a version.
ACCESSOR_END = (r"(?![\w-])(?!\.(?!(?:get|asProvider|toInt|toString|orNull|getOrElse|map|flatMap|isPresent)\b)\w)")

ANDROID_BASE = r"com\.android\.(library|application)|\bandroid\s*\{|libs\.plugins\.android"


def android_regex() -> "re.Pattern":
    """Matches a build file that is an Android module, including through the project's own
    convention plugins (build-logic plugins that apply the Android plugin) and any extra
    patterns listed under "androidPlugins" in lib-upgrade.config.json."""
    parts = [ANDROID_BASE] + list(_catalog.config().get("androidPlugins", []))
    try:
        plugins = _catalog.load().plugins
    except SystemExit:   # listing modules does not need a catalog
        plugins = {}
    for cls, pid in convention_plugins().items():
        src = glob.glob(os.path.join(root(), "build-logic", "**", f"{cls}.kt"), recursive=True)
        if src and re.search(r"com\.android\.|LibraryExtension|ApplicationExtension",
                             open(src[0], encoding="utf-8", errors="ignore").read()):
            parts.append(re.escape(pid))
            for alias, art in plugins.items():
                if art.group == pid:
                    parts.append(rf"libs\.plugins\.{re.escape(_catalog.accessor(alias))}{ACCESSOR_END}")
    return re.compile("|".join(parts))


def root() -> str:
    return _catalog.repo_root()


def settings_text() -> str:
    for name in ("settings.gradle", "settings.gradle.kts"):
        p = os.path.join(root(), name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                return fh.read()
    raise SystemExit("no settings.gradle(.kts) at the repo root")


def build_file(d: str) -> str | None:
    for name in ("build.gradle.kts", "build.gradle"):
        p = os.path.join(root(), d, name)
        if os.path.exists(p):
            return p
    return None


TEST_SOURCE_SETS = re.compile(r"^(test|commonTest|androidUnitTest|jvmTest|test[A-Z]\w*)$")


def has_unit_tests(module_dir: str) -> bool:
    """True when any unit-test source set (src/test, src/commonTest, src/androidUnitTest, ...) has sources."""
    src = os.path.join(module_dir, "src")
    try:
        sets = [n for n in os.listdir(src) if TEST_SOURCE_SETS.match(n)]
    except OSError:
        return False
    return any(f.endswith((".kt", ".java")) for n in sets
               for _, _, fs in os.walk(os.path.join(src, n)) for f in fs)


def projects() -> list[dict]:
    s = settings_text()
    overrides = dict(re.findall(r"""project\(\s*['"](:[^'"]+)['"]\s*\)\.projectDir\s*=\s*file\(\s*['"]([^'"]+)['"]\s*\)""", s))
    out, seen = [], set()
    android_rx = android_regex()
    unit_test = f"test{_catalog.config().get('unitTestVariant', 'Debug')}UnitTest"
    # include ':a', ':b'  /  include(":a", ":b")  /  include(":a")  - every quoted path in the call
    incs = []
    s_nocomments = re.sub(r"//[^\n]*|/\*.*?\*/", "", s, flags=re.S)
    for m in re.finditer(r"""(?<![\w.])include(?=[\s(])\s*(?:\(([^)]*)\)|((?:[^\n]*,[ \t]*\n)*[^\n]+))""", s_nocomments):
        incs += re.findall(r"""['"](:?[^'"]+)['"]""", m.group(1) or m.group(2) or "")
    for inc in incs:
        path = inc if inc.startswith(":") else ":" + inc
        if path in seen:
            continue
        seen.add(path)
        d = os.path.normpath(overrides.get(path) or path.lstrip(":").replace(":", "/"))
        bf = build_file(d)
        text = open(bf, encoding="utf-8", errors="ignore").read() if bf else ""
        android = bool(android_rx.search(text))
        has_tests = has_unit_tests(os.path.join(root(), d))
        out.append({"path": path, "dir": d, "build": bf, "android": android,
                    "task": f"{path}:{unit_test}" if android else f"{path}:test",
                    "has_tests": has_tests})
    app = [p for p in out if p["path"] == ":app"]
    if not app and build_file("app"):
        out.append({"path": ":app", "dir": "app", "build": build_file("app"), "android": True,
                    "task": f":app:{unit_test}", "has_tests": True})
    # The root build file declares plugin versions (`alias libs.plugins.x apply false`).
    if build_file("."):
        out.append({"path": ":", "dir": ".", "build": build_file("."), "android": False,
                    "task": "", "has_tests": False})
    return out


def convention_plugins() -> dict[str, str]:
    """implementationClass -> plugin id, from build-logic's registration block."""
    ids = {}
    for bf in glob.glob(os.path.join(root(), "build-logic", "**", "build.gradle*"), recursive=True):
        text = open(bf, encoding="utf-8", errors="ignore").read()
        for m in re.finditer(r"""id\s*=\s*["']([^"']+)["'][^}]*?implementationClass\s*=\s*["']([^"']+)["']""",
                             text, re.S):
            ids[m.group(2)] = m.group(1)
    return ids


def routes_for(cat: _catalog.Catalog, ref: str) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """((regex, label) routes, names a convention plugin could look up, hardcoded coordinates)."""
    libs = [a for a in cat.libraries.values() if a.version_ref == ref]
    plugs = [a for a in cat.plugins.values() if a.version_ref == ref]
    accs = [(rf"libs\.{re.escape(_catalog.accessor(a.alias))}{ACCESSOR_END}", f"libs.{_catalog.accessor(a.alias)}")
            for a in libs]
    accs += [(rf"libs\.plugins\.{re.escape(_catalog.accessor(p.alias))}{ACCESSOR_END}",
              f"libs.plugins.{_catalog.accessor(p.alias)}") for p in plugs]
    # Plugins are also applied by id string: `id 'com.google.firebase.crashlytics'`, `id("x")`.
    accs += [(rf"""\bid\s*\(?\s*['"]{re.escape(p.group)}['"]""", f"id '{p.group}'") for p in plugs if p.group]
    accs.append((rf"libs\.versions\.{re.escape(_catalog.accessor(ref))}{ACCESSOR_END}", f"libs.versions.{_catalog.accessor(ref)}"))
    lib_accs = {_catalog.accessor(a.alias) for a in libs}
    bundles = [b for b, members in cat.bundles.items()
               if any(_catalog.accessor(m) in lib_accs for m in members)]
    accs += [(rf"libs\.bundles\.{re.escape(_catalog.accessor(b))}{ACCESSOR_END}", f"libs.bundles.{_catalog.accessor(b)}")
             for b in bundles]
    names = [a.alias for a in libs] + bundles + [ref]
    coords = [f"{a.group}:{a.name}:" for a in libs if a.group and a.name]
    return accs, names, coords


def consumers(ref: str) -> list[dict]:
    cat = _catalog.load()
    if ref not in cat.versions:
        raise SystemExit(f"'{ref}' is not a [versions] key")
    accs, names, coords = routes_for(cat, ref)
    projs = projects()
    hits: dict[str, dict] = {}

    for p in projs:
        if not p["build"]:
            continue
        text = open(p["build"], encoding="utf-8", errors="ignore").read()
        how = [label for rx, label in accs if re.search(rx, text)]
        hard = [c + "..." for c in coords if c in text]
        if how or hard:
            hits[p["path"]] = {**p, "how": how, "hardcoded": hard}

    # Convention plugins that look the entry up by name reach every module applying them.
    pid = convention_plugins()
    for src in glob.glob(os.path.join(root(), "build-logic", "**", "*.kt"), recursive=True):
        text = open(src, encoding="utf-8", errors="ignore").read()
        found = [n for n in names if re.search(rf"""find(?:Library|Bundle|Version|Plugin)\(\s*["']{re.escape(n)}["']""", text)]
        if not found:
            continue
        cls = os.path.splitext(os.path.basename(src))[0]
        plugin = pid.get(cls)
        if not plugin:
            continue
        rx = re.compile(rf"libs\.plugins\.{re.escape(_catalog.accessor(plugin))}{ACCESSOR_END}|['\"]{re.escape(plugin)}['\"]")
        for p in projs:
            if p["build"] and rx.search(open(p["build"], encoding="utf-8", errors="ignore").read()):
                entry = hits.setdefault(p["path"], {**p, "how": [], "hardcoded": []})
                entry["how"].append(f"via convention plugin {plugin}")
    return sorted(hits.values(), key=lambda h: h["path"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("consumers"); p.add_argument("ref")
    p = sub.add_parser("tasks"); p.add_argument("ref")
    a = ap.parse_args()

    if a.cmd == "list":
        for p in projects():
            print(f"  {p['path']:<48} {p['dir']:<42} {'android' if p['android'] else 'jvm':<8} "
                  f"{p['task'].rsplit(':', 1)[-1] or '-':<18} {'tests' if p['has_tests'] else '-'}")
        return
    hits = consumers(a.ref)
    if a.cmd == "tasks":
        tasks = [h["task"] for h in hits if h["has_tests"]]
        print(" ".join(tasks) if tasks else "# no consuming module has unit tests - verify another way")
        return
    if not hits:
        print(f"{a.ref}: no consuming module found")
        return
    print(f"{a.ref}: {len(hits)} consuming module(s)")
    for h in hits:
        tests = "tests" if h["has_tests"] else "NO TESTS"
        print(f"  {h['path']:<46} {tests:<9} {', '.join(h['how'])}")
        for c in h["hardcoded"]:
            print(f"  {'':<46} {'':<9} HARDCODED outside the catalog: {c}")
    untested = [h["path"] for h in hits if not h["has_tests"]]
    if untested:
        print(f"\n  {len(untested)} consumer(s) have no unit tests: {', '.join(untested)}")


if __name__ == "__main__":
    main()
