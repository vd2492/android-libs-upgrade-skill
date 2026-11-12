#!/usr/bin/env python3
"""Look at the project this skill was pointed at and say what it found - run first, every time.

The scripts assume a Gradle project that keeps its dependency versions in a version catalog.
This reports, without changing anything: where the catalog is, which platform values it
could read (AGP, Kotlin, KSP, compileSdk, targetSdk, minSdk, Gradle), the application module,
how many modules were found, the installed Android Studio, and whether the tracker's one
Python dependency (openpyxl) is installed. A MISSING line says what to set in
lib-upgrade.config.json (see the skill's "Project settings" section).

Usage:
    preflight.py
Exit 1 when no version catalog is found or a required platform value (AGP, Kotlin, compileSdk)
could not be read; 0 otherwise.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _catalog  # noqa: E402


def line(status: str, what: str, detail: str = "") -> None:
    print(f"  {status:<8} {what:<22} {detail}")


def main() -> None:
    root = _catalog.repo_root()
    print(f"project: {root}")
    bad = False
    cfg = _catalog.config()
    line("OK" if cfg else "none", "lib-upgrade.config.json", "overrides: " + ", ".join(cfg) if cfg else "(optional)")

    try:
        path = _catalog.catalog_path()
        cat = _catalog.load()
        line("OK", "version catalog", f"{path}  ({len(cat.versions)} versions, {len(cat.libraries)} libraries, "
                                       f"{len(cat.plugins)} plugins)")
    except SystemExit as e:
        line("MISSING", "version catalog", str(e))
        sys.exit(1)

    plat = _catalog.platform()
    for dim, required in (("agp", True), ("kotlin", True), ("ksp", False), ("compileSdk", True),
                          ("targetSdk", False), ("minSdk", False), ("gradle", False)):
        if dim in plat:
            line("OK", dim, plat[dim])
        elif required:
            bad = True
            line("MISSING", dim, f'not found - map it: {{"toolchainKeys": {{"{dim}": "<catalog key>"}}}}')
        else:
            line("not set", dim, "(not needed for screening)")

    try:
        import modules
        projs = modules.projects()
        android = [p for p in projs if p["android"]]
        line("OK", "modules", f"{len(projs)} projects, {len(android)} Android, "
                              f"{sum(1 for p in projs if p['has_tests'])} with unit tests")
    except SystemExit as e:
        line("MISSING", "modules", str(e))

    import r8_check
    app = r8_check.app_dir()
    line("OK" if os.path.isdir(os.path.join(root, app)) else "MISSING", "application module", app)

    import studio
    found = studio.detect()
    if found:
        line("OK", "android studio", studio.describe(found[0]["key"]))
    else:
        line("not found", "android studio", "ask the user for their version (only matters for AGP upgrades)")

    try:
        import openpyxl  # noqa: F401
        line("OK", "openpyxl", "installed (the tracker needs it)")
    except ImportError:
        line("MISSING", "openpyxl", "python3 -m pip install --user openpyxl  (needed by tracker.py)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
