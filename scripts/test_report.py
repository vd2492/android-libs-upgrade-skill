#!/usr/bin/env python3
"""Report the unit-test results a run_gradle.sh run actually produced - fresh ones only.

A test task that did not execute still leaves XML behind that looks like a pass. Freshness is
judged by the `timestamp` attribute INSIDE each JUnit XML - when the tests ran - never by the
file's mtime: a task restored FROM-CACHE rewrites the files with brand-new mtimes without
running a single test (verified: file mtime 18:54, internal timestamp the previous day). A
module whose tests did not run after the run's start marker is reported STALE and is not
evidence of anything. (start-dev's ut_report.py aggregates totals too, but cannot tell fresh
from stale.)

A failure should be re-run at the SAME version before it is blamed on the bump: one
PacketScannerViewModelTest failure turned out to be a leaked coroutine from an earlier test
(UncaughtExceptionsBeforeTest) that passed on a --rerun-tasks re-run.

A verify/record/compareRoborazzi<Variant> task is read from its OWN results-summary.json, not
JUnit XML - it produces none. This file has no usable wall-clock field (its per-golden
"timestamp" is a monotonic nanoTime, not epoch time), so freshness here falls back to file
mtime and carries the same FROM-CACHE risk as JUnit XML's mtime without JUnit XML's internal
timestamp to catch it; that mtime match is reported as weaker evidence, not silently equated
with a JUnit result. `changed` or `added` > 0 on a verify task means goldens no longer match
and is reported as a failure - a renderer change can invalidate every committed golden.

Usage:
    test_report.py <run-name> <gradle test task>...
    test_report.py coil $(modules.py tasks coil)
Exit 1 on any failure or any stale module.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import re
import sys
import xml.etree.ElementTree as ET


def ran_at(suite) -> float | None:
    """Epoch seconds at which a JUnit <testsuite> executed, from its own timestamp attribute."""
    ts = suite.get("timestamp")
    if not ts:
        return None
    utc = ts.endswith("Z")
    try:
        dt = datetime.datetime.strptime(ts.rstrip("Z").split(".")[0], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    if utc:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


ROBORAZZI_TASK = re.compile(r"^(verify|record|compare|verifyAndRecord)Roborazzi([A-Z][A-Za-z0-9]*)$")


def roborazzi_result(root: str, proj: dict, tname: str) -> dict | None:
    """Read a Roborazzi task's own results-summary.json, keyed by build type (debug/staging/...)."""
    m = ROBORAZZI_TASK.match(tname)
    if not m:
        return None
    variant = re.sub(r"(?<!^)(?=[A-Z])", "-", m.group(2)).lower()
    f = os.path.join(root, proj["dir"], "build", "test-results", "roborazzi", variant,
                     "results-summary.json")
    if not os.path.exists(f):
        return None
    with open(f, encoding="utf-8") as fh:
        summary = json.load(fh)["summary"]
    summary["_mtime"] = os.path.getmtime(f)
    return summary

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _catalog  # noqa: E402
import modules  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", help="the name given to run_gradle.sh")
    ap.add_argument("tasks", nargs="+")
    a = ap.parse_args()

    root = _catalog.repo_root()
    a.run = _catalog.safe_name(a.run)
    marker = os.path.join(root, "build", "lib-upgrade", "runs", f"{a.run}.start")
    rc_file = os.path.join(root, "build", "lib-upgrade", "runs", f"{a.run}.rc")
    if not os.path.exists(marker):
        raise SystemExit(f"no start marker for run '{a.run}' - run it through run_gradle.sh first")
    since = os.path.getmtime(marker)
    if os.path.exists(rc_file):
        with open(rc_file, encoding="utf-8") as fh:
            rc = fh.read().strip()
    else:
        rc = "MISSING (still running?)"
    by_path = {p["path"]: p for p in modules.projects()}

    total = fails = errors = 0
    stale, failed = [], []
    print(f"run '{a.run}': Gradle exit {rc}\n")
    for task in a.tasks:
        path, _, tname = task.rpartition(":")
        proj = by_path.get(path)
        if not proj:
            print(f"  ?     {task}: not a project in settings.gradle")
            stale.append(task)
            continue

        rb = roborazzi_result(root, proj, tname)
        if rb is not None:
            age_min = (datetime.datetime.now().timestamp() - rb["_mtime"]) / 60
            if rb["_mtime"] < since:
                print(f"  STALE {path:<46} {tname} results are {age_min:.0f} min old, from before "
                      f"this run - not evidence")
                stale.append(path)
                continue
            diff = rb["changed"] + rb["added"]
            mark = "OK   " if diff == 0 else "FAIL "
            print(f"  {mark} {path:<46} {rb['total']:>5} goldens  unchanged={rb['unchanged']} "
                  f"changed={rb['changed']} added={rb['added']} recorded={rb['recorded']}  "
                  f"(mtime-based freshness, {age_min:.0f} min old - weaker than a JUnit timestamp)")
            total += rb["total"]; fails += diff
            if diff:
                failed.append(f"{path}: {rb['changed']} changed + {rb['added']} added golden(s) - "
                              f"goldens no longer match; a renderer change may have invalidated them")
            continue

        files = glob.glob(os.path.join(root, proj["dir"], "build", "test-results", tname, "*.xml"))
        suites = []
        for f in files:
            try:
                suites.append(ET.parse(f).getroot())
            except ET.ParseError:
                continue
        fresh = [s for s in suites if (ran_at(s) or 0) >= since]
        if not fresh:
            newest = max((ran_at(s) or 0 for s in suites), default=0)
            when = datetime.datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M") if newest else "never"
            print(f"  STALE {path:<46} tests last executed {when}, before this run - not evidence")
            stale.append(path)
            continue
        t = f_ = e = 0
        for r in fresh:
            t += int(r.get("tests", 0)); f_ += int(r.get("failures", 0)); e += int(r.get("errors", 0))
            for tc in r.iter("testcase"):
                bad = list(tc.iter("failure")) + list(tc.iter("error"))
                if bad:
                    msg = (bad[0].get("message") or "").strip().splitlines()
                    failed.append(f"{path} {tc.get('classname', '').split('.')[-1]}.{tc.get('name')}: "
                                  f"{msg[0][:160] if msg else ''}")
        total += t; fails += f_; errors += e
        mark = "OK   " if not (f_ or e) else "FAIL "
        print(f"  {mark} {path:<46} {t:>5} tests  {f_} failures  {e} errors")

    print(f"\n{len(a.tasks) - len(stale)} module(s) fresh: {total} tests, {fails} failures, {errors} errors")
    if failed:
        print("\nFailures (re-run at the same version before blaming the bump):")
        for f in failed:
            print(f"  {f}")
    if stale:
        print(f"\nSTALE: {', '.join(stale)} - these produced no fresh results and prove nothing")
    sys.exit(1 if fails or errors or stale or rc != "0" else 0)


if __name__ == "__main__":
    main()
