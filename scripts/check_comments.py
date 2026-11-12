#!/usr/bin/env python3
"""Keep the comments next to the libs true - run after EVERY bump, before the commit message.

A prose rule to keep comments fresh already existed and still let this through in one session:
map_utils and maps_compose saying "project is on 2.2.20" after Kotlin moved to 2.3.20,
moengage_inbox_ui still "blocked" after its blocker cleared, and the NewRelic pin sitting past
its own trigger. Each check below is aimed at one of those.

Checks (diff mode runs all; --audit runs the ones that need no diff):
  OWN        the bumped ref's value moved but its comment did not
  MENTION    another comment names the bumped ref, or cites a toolchain value it replaced
  CLAIM      "project is on X" / "compiles against N" / "we are X" where X is not a current value
  CONDITION  "retry after / until / re-check before / when ... lands" whose subject (AGP, Gradle,
             Kotlin, KSP, compileSdk, targetSdk) has moved since the comment was written
  RELATION   "lockstep with <ref>", "matches <bom> exactly", "<key> = "v"", "<key> version (v)"
  LINEREF    "(line N)" that no longer points at the key it names
  TRACKER    a blocked tracker row with no catalog comment, or a "blocked" comment with no
             blocked row, or tracker text making a stale CLAIM

Usage:
    check_comments.py                    # working tree vs HEAD (after a bump)
    check_comments.py --base <rev> --head <rev>
    check_comments.py --audit            # standing sweep of the whole catalog
    check_comments.py --ack coil,glide   # confirmed still accurate - suppress these keys
Exit 1 while any finding is unacknowledged.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from functools import lru_cache

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _catalog  # noqa: E402

CONDITION = re.compile(r"retry (?:after|when)|\buntil\b|re-check[^.;]*before|when [^.;]*\blands\b|"
                       r"after [^.;]*\b(?:moves|bump|lands)\b", re.IGNORECASE)
DIMENSIONS = {
    "agp": re.compile(r"\bAGP\b|android gradle plugin", re.IGNORECASE),
    "gradle": re.compile(r"\bGradle\b(?!\s+plugin)"),
    "kotlin": re.compile(r"\bkotlin(?:_plugin)?\b|jetbrain_kotlin", re.IGNORECASE),
    "ksp": re.compile(r"\bKSP\b"),
    "compileSdk": re.compile(r"compileSdk"),
    "targetSdk": re.compile(r"targetSdk"),
}
CLAIMS = [
    re.compile(r"(?:this |the )?project (?:is on|compiles against|targets) ([0-9][0-9.]*[0-9])", re.IGNORECASE),
    re.compile(r"\bwe(?:'re| are)(?: on)? ([0-9][0-9.]*[0-9]|[0-9]+)\b", re.IGNORECASE),
    re.compile(r"compiles against ([0-9]+)", re.IGNORECASE),
]
LOCKSTEP = re.compile(r"lockstep with ([A-Za-z0-9_\-]+)", re.IGNORECASE)
MATCHES = re.compile(r"matches ([A-Za-z0-9_\-]+) exactly", re.IGNORECASE)
QUOTED = re.compile(r"""\b([A-Za-z][A-Za-z0-9_\-]*)\s*=\s*"([^"]+)\"""")
PAREN_VERSION = re.compile(r"\b([A-Za-z][A-Za-z0-9_\-]*) version \(([0-9][0-9.]*)\)")
LINE_REF = re.compile(r"\bline (\d+)\b")
COMPILE_SDK_LANDS = re.compile(r"when compileSdk (\d+) lands", re.IGNORECASE)
BLOCKED_WORD = re.compile(r"\bblocked\b|\bheld\b", re.IGNORECASE)


class Finding:
    def __init__(self, code: str, key: str, line: int, why: str, comment: str):
        self.code, self.key, self.line, self.why, self.comment = code, key, line, why, comment

    def __str__(self) -> str:
        text = self.comment if len(self.comment) <= 150 else self.comment[:147] + "..."
        return f"  [{self.code}] {self.key} (L{self.line}): {self.why}\n        # {text}"


def entries(cat: _catalog.Catalog) -> list[tuple[str, int, str]]:
    """(key, line, comment) for every commented version line and every standalone comment."""
    out = [(v.key, v.line, v.comment) for v in cat.versions.values() if v.comment]
    out += [(f"#L{line}", line, text) for line, _, text in cat.standalone_comments]
    return out


def dims_in(text: str) -> set[str]:
    return {d for d, rx in DIMENSIONS.items() if rx.search(text)}


def token(text: str, value: str) -> bool:
    return re.search(rf"(?<![\w.]){re.escape(value)}(?![\w]|\.\d)", text) is not None


@lru_cache(maxsize=None)
def platform_at(rev: str | None) -> tuple:
    return tuple(sorted(_catalog.platform(rev).items()))


def blame(line: int, rev: str | None) -> str | None:
    """Commit that last touched `line` (None when it is uncommitted)."""
    cmd = ["git", "blame", "--porcelain", "-L", f"{line},{line}"]
    if rev:
        cmd.append(rev)
    cmd += ["--", _catalog.catalog_path()]
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=_catalog.repo_root())
    sha = out.stdout.split(" ", 1)[0] if out.returncode == 0 and out.stdout else ""
    return None if not sha or set(sha) == {"0"} else sha


def line_of(cat: _catalog.Catalog, key: str) -> int | None:
    if key in cat.versions:
        return cat.versions[key].line
    for i, raw in enumerate(cat.lines, 1):
        if re.match(rf"^\s*{re.escape(key)}\s*=", raw):
            return i
    return None


def check_claims(cat, plat: dict[str, str], key: str, line: int, text: str, code: str = "CLAIM"):
    values = set(plat.values())
    for rx in CLAIMS:
        for m in rx.finditer(text):
            v = m.group(1).rstrip(".")
            if v not in values:
                yield Finding(code, key, line, f"claims the project is on {v}, but no current platform "
                              f"value is {v} ({', '.join(f'{k} {x}' for k, x in sorted(plat.items()))})",
                              text)


def check_relations(cat, key: str, line: int, text: str, bom_check: bool):
    own = cat.versions.get(key)
    for m in LOCKSTEP.finditer(text):
        other = m.group(1)
        if own and other in cat.versions and cat.versions[other].value != own.value:
            yield Finding("RELATION", key, line, f"claims lockstep with {other}, but {key}={own.value} "
                          f"and {other}={cat.versions[other].value}", text)
    for m in QUOTED.finditer(text):
        k, v = m.group(1), m.group(2)
        if k in cat.versions and cat.versions[k].value != v:
            yield Finding("RELATION", key, line, f'says {k} = "{v}", but it is "{cat.versions[k].value}"', text)
    for m in PAREN_VERSION.finditer(text):
        k, v = m.group(1), m.group(2)
        if k in cat.versions and cat.versions[k].value != v:
            yield Finding("RELATION", key, line, f"says {k} version ({v}), but it is {cat.versions[k].value}", text)
    if bom_check and own:
        for m in MATCHES.finditer(text):
            yield from check_bom_match(cat, key, line, text, m.group(1))


def check_bom_match(cat, key: str, line: int, text: str, bom_ref: str):
    import _maven
    if bom_ref not in cat.versions:
        return
    boms = [a for a in cat.artifacts_for(bom_ref) if not a.is_plugin]
    mine = [a for a in cat.artifacts_for(key) if not a.is_plugin]
    if not boms or not mine:
        return
    managed = {(g, a): v for g, a, v in _maven.bom_managed(boms[0].group, boms[0].name,
                                                          cat.versions[bom_ref].value)}
    if not managed:
        yield Finding("RELATION", key, line, f"could not read {bom_ref}'s BOM to verify 'matches exactly' "
                      f"- verify by hand", text)
        return
    for art in mine:
        pinned = managed.get((art.group, art.name))
        if pinned and pinned != cat.versions[key].value:
            yield Finding("RELATION", key, line, f"claims to match {bom_ref} exactly, but "
                          f"{bom_ref} {cat.versions[bom_ref].value} pins {art.name} {pinned} and this "
                          f"is {cat.versions[key].value}", text)


def check_linerefs(cat, key: str, line: int, text: str):
    for m in LINE_REF.finditer(text):
        n = int(m.group(1))
        named = [k for k in list(cat.versions) + list(cat.libraries) + list(cat.plugins)
                 if re.search(rf"(?<![\w-]){re.escape(k)}(?![\w-])", text)]
        if not named:
            yield Finding("LINEREF", key, line, f"cites line {n} but names no catalog key - a bare line "
                          f"number rots on every edit", text)
            continue
        actual = {k: line_of(cat, k) for k in named}
        if n not in actual.values():
            where = ", ".join(f"{k} is on line {v}" for k, v in actual.items() if v)
            yield Finding("LINEREF", key, line, f"cites line {n}, but {where}", text)


def check_conditions_audit(cat, key: str, line: int, text: str, head: str | None, plat: dict[str, str]):
    if not CONDITION.search(text):
        return
    m = COMPILE_SDK_LANDS.search(text)
    if m and int(plat.get("compileSdk", "0")) >= int(m.group(1)):
        yield Finding("CONDITION", key, line, f"waits for compileSdk {m.group(1)}, and the project is now "
                      f"on {plat['compileSdk']} - the condition is MET", text)
        return
    dims = dims_in(text)
    if not dims:
        return
    sha = blame(line, head)
    if not sha:
        return  # written in this very change
    then = dict(platform_at(sha))
    moved = [f"{d} {then.get(d)} -> {plat.get(d)}" for d in sorted(dims) if then.get(d) != plat.get(d)]
    if moved:
        yield Finding("CONDITION", key, line, f"conditional on {', '.join(sorted(dims))}; since this comment "
                      f"was written ({sha[:10]}): {'; '.join(moved)} - the condition may be MET", text)


def check_tracker(cat, plat: dict[str, str], xlsx: str | None):
    try:
        import tracker
    except SystemExit:
        return
    path = tracker.xlsx_path(xlsx)
    rows = tracker.rows_all(path)
    if not rows:
        return
    tracked = {row["lib name"]: row for _, row in rows}
    for ref, row in tracked.items():
        status = row.get("status") or "pending"
        v = cat.versions.get(ref)
        if status == "blocked" and v is not None and not v.comment:
            yield Finding("TRACKER", ref, v.line, "blocked in the tracker, but the catalog has no comment "
                          "saying why", "")
        if status == "done" and v is not None and str(row.get("new version") or "") not in ("", v.value):
            yield Finding("TRACKER", ref, v.line, f"tracker says done at {row.get('new version')}, but the "
                          f"catalog is at {v.value} - record the version actually shipped (and mark what "
                          f"is still held back)", v.comment)
        reason = f"{row.get('code changes') or ''} {row.get('build result') or ''}"
        for f in check_claims(cat, plat, ref, v.line if v else 0, reason, code="TRACKER"):
            f.why = "tracker row text " + f.why
            yield f
    for v in cat.versions.values():
        if v.comment and BLOCKED_WORD.search(v.comment):
            row = tracked.get(v.key)
            if row is None:
                yield Finding("TRACKER", v.key, v.line, "catalog comment records a blocked/held upgrade that "
                              "the tracker has no row for", v.comment)


def run(base: str | None, head: str | None, audit: bool, xlsx: str | None, bom_check: bool) -> list[Finding]:
    head_cat = _catalog.load(head)
    plat = dict(platform_at(head))
    found: list[Finding] = []

    if not audit:
        base_cat = _catalog.load(base)
        base_plat = dict(platform_at(base))
        changed = {k: (base_cat.versions[k].value, v.value) for k, v in head_cat.versions.items()
                   if k in base_cat.versions and base_cat.versions[k].value != v.value}
        moved_dims = {d for d in set(plat) | set(base_plat) if plat.get(d) != base_plat.get(d)}
        base_texts = {c for _, _, c in entries(base_cat)}
        for key, (old, new) in changed.items():
            v = head_cat.versions[key]
            if v.comment and base_cat.versions[key].comment == v.comment:
                found.append(Finding("OWN", key, v.line, f"value moved {old} -> {new} but its comment did not "
                                     f"- confirm it still describes {new}", v.comment))
        for key, line, text in entries(head_cat):
            if text not in base_texts:
                continue  # rewritten in this change: the author has already looked at it
            for ref, (old, new) in changed.items():
                if ref == key:
                    continue
                if re.search(rf"(?<![\w-]){re.escape(ref)}(?![\w-])", text):
                    found.append(Finding("MENTION", key, line, f"names {ref}, which moved {old} -> {new}", text))
                elif ref in _catalog.TOOLCHAIN_KEYS and token(text, old):
                    found.append(Finding("MENTION", key, line, f"cites {old}, the value {ref} just moved away "
                                         f"from ({old} -> {new})", text))
            if CONDITION.search(text):
                hit = dims_in(text) & moved_dims
                if hit:
                    moves = "; ".join(f"{d} {base_plat.get(d)} -> {plat.get(d)}" for d in sorted(hit))
                    found.append(Finding("CONDITION", key, line, f"conditional on {', '.join(sorted(hit))}, "
                                         f"which this change moved ({moves}) - the condition may be MET", text))

    for key, line, text in entries(head_cat):
        found += list(check_claims(head_cat, plat, key, line, text))
        found += list(check_relations(head_cat, key, line, text, bom_check))
        found += list(check_linerefs(head_cat, key, line, text))
        if audit:
            found += list(check_conditions_audit(head_cat, key, line, text, head, plat))
    if head is None:  # the tracker describes the working tree, not a historical revision
        found += list(check_tracker(head_cat, plat, xlsx))

    uniq, seen = [], set()
    for f in found:
        sig = (f.code, f.key, f.why)
        if sig not in seen:
            seen.add(sig)
            uniq.append(f)
    return uniq


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="HEAD", help="git revision to diff against (default HEAD)")
    ap.add_argument("--head", default=None, help="git revision to check (default: working tree)")
    ap.add_argument("--audit", action="store_true", help="standing sweep instead of a diff")
    ap.add_argument("--xlsx", default=None)
    ap.add_argument("--no-bom", action="store_true", help="skip BOM lookups (offline)")
    ap.add_argument("--ack", default="", help="comma-separated keys confirmed accurate")
    a = ap.parse_args()

    findings = run(a.base, a.head, a.audit, a.xlsx, not a.no_bom)
    acked = {k.strip() for k in a.ack.split(",") if k.strip()}
    open_ = [f for f in findings if f.key not in acked]
    label = "audit of " + (a.head or "working tree") if a.audit else f"{a.base} -> {a.head or 'working tree'}"
    print(f"comment check ({label}): {len(open_)} open, {len(findings) - len(open_)} acknowledged\n")
    for code in ("OWN", "MENTION", "CONDITION", "CLAIM", "RELATION", "LINEREF", "TRACKER"):
        group = [f for f in open_ if f.code == code]
        for f in group:
            print(f)
    if open_:
        print("\nEach finding must be UPDATED, or confirmed accurate and passed to --ack, "
              "before the commit message is written.")
    sys.exit(1 if open_ else 0)


if __name__ == "__main__":
    main()
