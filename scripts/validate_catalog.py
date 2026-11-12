#!/usr/bin/env python3
"""Validate gradle/libs.versions.toml after an edit: shape, duplicates, and exactly what moved.

Run after every catalog edit, including comment-only edits. A comment edit twice produced
`compose_bom = "2026.09.00" blocked: ...` - the new version and a missing `#` - which reads as a
value change nobody intended. And a `name = ` replace can hit two lines (the version and the
library alias both start with `newrelic = `); --expect turns "only these refs may change" into
a hard check instead of a hope.

Usage:
    validate_catalog.py                         # working tree vs HEAD
    validate_catalog.py --expect coil           # fail if anything other than coil moved
    validate_catalog.py --expect ""             # comment-only edit: nothing may move
    validate_catalog.py --base <rev>
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _catalog  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="HEAD")
    ap.add_argument("--head", default=None, help="git revision (default: working tree)")
    ap.add_argument("--expect", default=None,
                    help="comma-separated refs allowed to change; empty string means none")
    a = ap.parse_args()

    base, head = _catalog.load(a.base), _catalog.load(a.head)
    problems = []
    for line, raw in head.malformed:
        problems.append(f"MALFORMED L{line}: {raw.strip()[:120]}")
    for key in head.duplicate_keys:
        problems.append(f"DUPLICATE [versions] key: {key}")

    changed = {k: (base.versions[k].value, v.value) for k, v in head.versions.items()
               if k in base.versions and base.versions[k].value != v.value}
    added = sorted(set(head.versions) - set(base.versions))
    removed = sorted(set(base.versions) - set(head.versions))
    lib_changes = sorted(k for k in set(base.libraries) | set(head.libraries)
                         if (base.libraries.get(k) and head.libraries.get(k)
                             and vars(base.libraries[k]) != vars(head.libraries[k]))
                         or (k in base.libraries) != (k in head.libraries))

    print(f"catalog vs {a.base}:")
    for k, (old, new) in sorted(changed.items()):
        print(f"  CHANGED {k}: {old} -> {new}")
    for k in added:
        print(f"  ADDED   {k} = {head.versions[k].value}")
    for k in removed:
        print(f"  REMOVED {k} (was {base.versions[k].value})")
    for k in lib_changes:
        print(f"  LIBRARY {k}: declaration changed")
    if not (changed or added or removed or lib_changes):
        print("  no version values changed")

    if a.expect is not None:
        allowed = {x.strip() for x in a.expect.split(",") if x.strip()}
        unexpected = (set(changed) | set(added) | set(removed)) - allowed
        for k in lib_changes:
            art = head.libraries.get(k) or base.libraries.get(k)
            if not ({art.version_ref, (base.libraries.get(k) or art).version_ref} & allowed):
                unexpected.add(f"library:{k}")
        for k in sorted(set(base.plugins) | set(head.plugins)):
            b, h = base.plugins.get(k), head.plugins.get(k)
            if b != h and not ({(h or b).version_ref, (b or h).version_ref} & allowed):
                unexpected.add(f"plugin:{k}")
        for k in sorted(set(base.bundles) | set(head.bundles)):
            if base.bundles.get(k) != head.bundles.get(k):
                unexpected.add(f"bundle:{k}")
        if unexpected:
            problems.append(f"UNEXPECTED change(s) outside --expect: {', '.join(sorted(unexpected))}")
        missing = allowed - set(changed) - set(added) - set(removed)
        if missing:
            problems.append(f"EXPECTED change(s) did not happen: {', '.join(sorted(missing))}")

    if problems:
        print("\nFAIL:")
        for p in problems:
            print(f"  {p}")
        sys.exit(1)
    print("\nOK: no malformed lines, no duplicate keys" + ("" if a.expect is None else ", scope as expected"))


if __name__ == "__main__":
    main()
