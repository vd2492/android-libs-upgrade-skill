#!/usr/bin/env python3
"""Group the pending Tier 1 (patch) rows into batches the user can approve as ONE bump.

The default loop is one lib, one test run, one device build, one commit. For a patch that is
mostly ceremony: patches rarely break anything, and eighteen device builds cost far more than
the risk they remove. A batch bumps the group together, runs the union of the consuming
modules' tests once, and hands off one device build and one commit. It is opt-in - the user
says yes at the queue - and a batch that fails is halved to find the culprit, so the other
patches still land.

Only rows that would pass through the normal loop untouched are eligible: Tier 1, passes both
gates at its highest passing version (screen_gates), no known code change, and not on
references/staging-only-libs.txt or "stagingOnly" in lib-upgrade.config.json (those need their own
staging build, so they stay single).

Usage:
    batch.py plan [--max 20]       # eligible refs, their target versions, the union of test tasks
    batch.py tasks <ref>...        # union of the Gradle test tasks for these refs, de-duplicated
    batch.py split <ref>...        # two halves of a failing batch, to bisect
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _catalog  # noqa: E402
import _maven  # noqa: E402
import modules  # noqa: E402
import screen_gates  # noqa: E402

STAGING_ONLY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references",
                            "staging-only-libs.txt")


def staging_only() -> dict[str, str]:
    """ref -> reason, from references/staging-only-libs.txt (`ref  # reason`)."""
    out: dict[str, str] = {}
    try:
        with open(STAGING_ONLY, encoding="utf-8") as fh:
            for raw in fh:
                body, _, reason = raw.partition("#")
                if body.strip():
                    out[body.strip()] = reason.strip()
    except FileNotFoundError:
        pass
    except OSError as e:  # unreadable is not the same as absent: ignoring it would drop the exclusions
        raise SystemExit(f"cannot read {STAGING_ONLY}: {e}")
    configured = _catalog.config().get("stagingOnly", {})   # the project's own list, in lib-upgrade.config.json
    if isinstance(configured, dict):
        out.update({str(k): str(v) for k, v in configured.items()})
    return out


HOLD = re.compile(r"\bblocked\b|\bmoves? with\b|\bmove together\b|lockstep|device pass", re.IGNORECASE)


def held_by_comment(cat, ref: str) -> str | None:
    """Why the catalog line itself says this ref must not ride in a batch, else None.

    The comments are the repo's record of per-lib constraints (policy: every blocked or coupled
    ref says why): a blocker, a coupled set that must move together, or a QA pass this lib needs
    on its own. A batch that ignored them would undo the normal loop's safeguards.
    """
    comment = cat.versions[ref].comment or ""
    m = HOLD.search(comment)
    return f"its catalog comment says so ({m.group(0)}): {comment[:90]}" if m else None


def union_tasks(refs: list[str]) -> list[str]:
    tasks: list[str] = []
    for ref in refs:
        for h in modules.consumers(ref):
            if h["has_tests"] and h["task"] not in tasks:
                tasks.append(h["task"])
    return tasks


def cmd_plan(a) -> None:
    import tracker  # needs openpyxl; only `plan` reads the tracker
    cat = _catalog.load()
    plat = cat.toolchain()
    compile_sdk, kotlin = int(plat["compileSdk"]), plat["kotlin"]
    rows = [r for part, r in tracker.rows_all(tracker.xlsx_path(a.xlsx))
            if part == "libs" and (r.get("status") or "pending") == "pending"]
    if not rows:
        print("no pending Lib Upgrades rows in the tracker - run scan_catalog.py and tracker.py init first")
        return
    held = staging_only()
    eligible: list[tuple[str, str, str]] = []
    excluded: list[tuple[str, str]] = []
    for r in rows:
        ref = r["lib name"]
        if str(r.get("tier") or "") not in ("Tier 1", "T1"):
            continue
        if ref not in cat.versions or not any(x for x in cat.artifacts_for(ref)
                                              if not x.is_plugin and x.group and x.name):
            excluded.append((ref, "not a screenable catalog ref (no library artifact to screen)"))
        elif str(r.get("code changes") or "").startswith("DIVERGED"):
            excluded.append((ref, "DIVERGED - the ref's artifacts are on different versions"))
        elif ref in held:
            excluded.append((ref, f"staging-only: {held[ref]}"))
        elif held_by_comment(cat, ref):
            excluded.append((ref, held_by_comment(cat, ref)))
        else:
            try:
                best = screen_gates.bisect(cat, ref, compile_sdk, kotlin, quiet=True)
            except SystemExit as e:   # unreachable repo / not published: stays on the one-lib loop
                excluded.append((ref, str(e)))
                continue
            current = cat.versions[ref].value
            if best is None:
                excluded.append((ref, "no stable version above the current one passes both gates"))
            elif _maven.tier(current, best) != "T1":
                excluded.append((ref, f"highest passing version {best} is not a patch"))
            else:
                eligible.append((ref, current, best))
    if not eligible:
        print("no patch is eligible for a batch - use the normal one-lib loop")
    else:
        size = max(1, a.max)
        chunks = [eligible[i:i + size] for i in range(0, len(eligible), size)]
        for n, chunk in enumerate(chunks, 1):
            print(f"BATCH {n} of {len(chunks)}: {len(chunk)} patch(es)")
            for ref, cur, best in chunk:
                print(f"  {ref:<32} {cur:<14} -> {best}")
            tasks = union_tasks([c[0] for c in chunk])
            print(f"  test tasks ({len(tasks)}): {' '.join(tasks) if tasks else '# none - no consumer has unit tests'}")
            if len(tasks) > 20:
                print("  more than ~20 modules: run them in groups - a foreground run is capped at 10 minutes")
    if excluded:
        print("\nstay on the one-lib loop:")
        for ref, why in excluded:
            print(f"  {ref:<32} {why}")


def cmd_tasks(a) -> None:
    tasks = union_tasks(a.refs)
    print(" ".join(tasks) if tasks else "# no consuming module has unit tests - verify another way")


def cmd_split(a) -> None:
    refs = a.refs
    if len(refs) < 2:
        print(f"{refs[0] if refs else '(none)'}: a single ref - this is the culprit, back to the one-lib loop")
        return
    mid = (len(refs) + 1) // 2
    print("FIRST :", " ".join(refs[:mid]))
    print("SECOND:", " ".join(refs[mid:]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("--max", type=int, default=20); p.add_argument("--xlsx")
    p = sub.add_parser("tasks"); p.add_argument("refs", nargs="+")
    p = sub.add_parser("split"); p.add_argument("refs", nargs="+")
    a = ap.parse_args()
    {"plan": cmd_plan, "tasks": cmd_tasks, "split": cmd_split}[a.cmd](a)


if __name__ == "__main__":
    main()
