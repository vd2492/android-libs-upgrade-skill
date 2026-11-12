#!/usr/bin/env python3
"""Create, update and read the upgrade tracker workbook - the skill's resumable state.

The workbook has one tab per part of the skill ("SDK & Toolchain", "Lib Upgrades") with the
same columns. The status column IS the state: `next` finds where an interrupted run resumes,
so nothing depends on a conversation surviving.

Columns are always resolved by header name, never by index. Inserting three columns once
shifted `status` from column 6 to 9, and an index-based writer silently overwrote two other
cells on the firebase_bom row.

Usage:
    tracker.py init --scan build/lib-upgrade/scan.json [--part libs|sdk]
    tracker.py set <ref> [--part libs|sdk] --status done --build "..." [--changes "..."]
    tracker.py get <ref>
    tracker.py list [--status blocked] [--part libs|sdk]
    tracker.py next
    tracker.py summary
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _catalog  # noqa: E402

try:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
except ImportError:  # pragma: no cover
    raise SystemExit("openpyxl is required: python3 -m pip install --user openpyxl")

DEFAULT_XLSX = "lib-upgrade-tracker.xlsx"
TABS = {"sdk": "SDK & Toolchain", "libs": "Lib Upgrades"}
COLUMNS = ["lib name", "existing version", "new version", "official docs", "code changes",
           "dev testing", "qa testing", "modules affected", "tier", "status", "build result",
           "changelog highlights"]
STATUSES = ["pending", "awaiting device", "done", "blocked", "skipped"]
FILL = {"done": "C6EFCE", "blocked": "FFC7CE", "skipped": "D9D9D9", "awaiting device": "BDD7EE",
        "pending": "FFF9C4"}  # awaiting device is blue, not yellow, so it never reads too close to pending
WIDTHS = {"lib name": 26, "existing version": 16, "new version": 16, "official docs": 40,
          "code changes": 70, "dev testing": 50, "qa testing": 50, "modules affected": 30,
          "tier": 8, "status": 16, "build result": 70, "changelog highlights": 60}
TIER_LABEL = {"T1": "Tier 1", "T2": "Tier 2", "T3": "Tier 3"}


def xlsx_path(arg: str | None) -> str:
    return arg or os.path.join(_catalog.repo_root(), DEFAULT_XLSX)


def open_book(path: str):
    if os.path.exists(path):
        return openpyxl.load_workbook(path)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    return wb


def sheet(wb, part: str, create: bool = False):
    name = TABS[part]
    if name in wb.sheetnames:
        return wb[name]
    if not create:
        return None
    ws = wb.create_sheet(name, 0 if part == "sdk" else None)
    for col, title in enumerate(COLUMNS, 1):
        cell = ws.cell(row=1, column=col, value=title)
        cell.font = Font(name="Arial", size=10, bold=True)
        ws.column_dimensions[cell.column_letter].width = WIDTHS[title]
    ws.freeze_panes = "B2"
    return ws


def headers(ws) -> dict[str, int]:
    hdr = {c.value: c.column for c in ws[1] if c.value}
    for title in COLUMNS:  # older workbooks gain new columns on first touch, appended at the end
        if title not in hdr:
            col = ws.max_column + 1
            cell = ws.cell(row=1, column=col, value=title)
            cell.font = Font(name="Arial", size=10, bold=True)
            ws.column_dimensions[cell.column_letter].width = WIDTHS[title]
            hdr[title] = col
    missing = [c for c in ("lib name", "status") if c not in hdr]
    if missing:
        raise SystemExit(f"tab '{ws.title}' is missing columns {missing}")
    return hdr


def find_row(ws, hdr: dict[str, int], ref: str) -> int | None:
    for r in range(2, ws.max_row + 1):
        if ws.cell(row=r, column=hdr["lib name"]).value == ref:
            return r
    return None


def style_status(cell) -> None:
    """Set the status cell's fill and font from its CURRENT value - always both, never one.

    A status with no FILL entry (pending) must explicitly clear any fill a PRIOR status left
    behind, or a row that goes blocked -> pending keeps reading red. This bit a real tracker:
    six rows re-bisected from blocked to pending kept blocked's FFC7CE fill because the old
    version only set a fill when one was defined for the new status.
    """
    status = cell.value or "pending"
    cell.fill = PatternFill("solid", fgColor=FILL[status]) if status in FILL else PatternFill(fill_type=None)
    cell.font = Font(name="Arial", size=10, bold=status in ("done", "blocked"))


def write(ws, hdr: dict[str, int], row: int, values: dict[str, str | None]) -> None:
    for column, value in values.items():
        if value is None:
            continue
        if column not in hdr:
            raise SystemExit(f"tab '{ws.title}' has no '{column}' column")
        cell = ws.cell(row=row, column=hdr[column], value=value)
        cell.font = Font(name="Arial", size=10)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        if column == "status":
            style_status(cell)


def rows(ws) -> list[dict[str, str]]:
    hdr = headers(ws)
    out = []
    for r in range(2, ws.max_row + 1):
        name = ws.cell(row=r, column=hdr["lib name"]).value
        if name:
            out.append({col: ws.cell(row=r, column=idx).value for col, idx in hdr.items()})
    return out


def rows_all(path: str) -> list[tuple[str, dict[str, str]]]:
    """(part, row) for every row in every tab - used by the other scripts."""
    if not os.path.exists(path):
        return []
    wb = openpyxl.load_workbook(path)
    out = []
    for part in ("sdk", "libs"):
        ws = sheet(wb, part)
        if ws is not None:
            out += [(part, row) for row in rows(ws)]
    return out


def cmd_init(a) -> None:
    with open(a.scan, encoding="utf-8") as fh:
        scan = json.load(fh)
    wb = open_book(a.xlsx)
    added = kept = 0
    for entry in scan["refs"]:
        if entry["state"] not in ("behind", "diverged"):
            continue
        part = "sdk" if entry.get("toolchain") else "libs"
        if a.part and part != a.part:
            continue
        ws = sheet(wb, part, create=True)
        hdr = headers(ws)
        if find_row(ws, hdr, entry["ref"]):
            kept += 1  # never overwrite a row that already carries state
            continue
        row = ws.max_row + 1
        write(ws, hdr, row, {
            "lib name": entry["ref"],
            "existing version": entry["current"],
            "new version": entry.get("latest") or "",
            "official docs": entry.get("docs", ""),
            "modules affected": entry.get("modules", ""),
            "tier": TIER_LABEL.get(entry.get("tier", ""), entry.get("tier", "")),
            "status": "pending",
            "code changes": "DIVERGED: " + entry["note"] if entry["state"] == "diverged" else "",
        })
        added += 1
    wb.save(a.xlsx)
    print(f"tracker: {added} row(s) added, {kept} already present (left untouched) -> {a.xlsx}")


def cmd_set(a) -> None:
    if a.status and a.status not in STATUSES:
        raise SystemExit(f"status must be one of {STATUSES}")
    wb = open_book(a.xlsx)
    parts = [a.part] if a.part else ["libs", "sdk"]
    for part in parts:
        ws = sheet(wb, part)
        if ws is None:
            continue
        hdr = headers(ws)
        row = find_row(ws, hdr, a.ref)
        if row:
            write(ws, hdr, row, {"status": a.status, "build result": a.build,
                                 "code changes": a.changes, "new version": a.new_version,
                                 "dev testing": a.dev, "qa testing": a.qa,
                                 "modules affected": a.modules, "tier": a.tier,
                                 "official docs": a.docs,
                                 "changelog highlights": a.highlights})
            wb.save(a.xlsx)
            print(f"  {a.ref} [{TABS[part]}]: " + (f"status={a.status}" if a.status else "updated"))
            return
    raise SystemExit(f"{a.ref} not found in {[TABS[p] for p in parts]}")


def cmd_get(a) -> None:
    for part, row in rows_all(a.xlsx):
        if row["lib name"] == a.ref:
            print(f"[{TABS[part]}]")
            for col in COLUMNS:
                if row.get(col):
                    print(f"  {col}: {row[col]}")
            return
    raise SystemExit(f"{a.ref} not found")


def cmd_list(a) -> None:
    for part, row in rows_all(a.xlsx):
        if a.part and part != a.part:
            continue
        status = row.get("status") or "pending"
        if a.status and status != a.status:
            continue
        print(f"  [{part}] {row['lib name']:<32} {str(row.get('existing version')):<16} -> "
              f"{str(row.get('new version')):<16} {status}")


def cmd_next(a) -> None:
    """Resume point: SDK tab before libs; an awaiting-device row before a fresh pending one."""
    all_rows = rows_all(a.xlsx)
    for wanted in ("awaiting device", "pending"):
        for part in ("sdk", "libs"):
            for p, row in all_rows:
                if p == part and (row.get("status") or "pending") == wanted:
                    print(f"NEXT [{TABS[part]}] {row['lib name']}: {row.get('existing version')} -> "
                          f"{row.get('new version')} ({wanted})")
                    return
    print("NEXT: nothing pending - every row is done, blocked or skipped")


def cmd_summary(a) -> None:
    for part in ("sdk", "libs"):
        counts: dict[str, int] = {}
        for p, row in rows_all(a.xlsx):
            if p == part:
                s = row.get("status") or "pending"
                counts[s] = counts.get(s, 0) + 1
        if counts:
            total = sum(counts.values())
            detail = " · ".join(f"{counts[s]} {s}" for s in STATUSES if s in counts)
            print(f"{TABS[part]}: {total} rows · {detail}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xlsx", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("--scan", required=True); p.add_argument("--part", choices=TABS)
    p = sub.add_parser("set"); p.add_argument("ref"); p.add_argument("--part", choices=TABS)
    for opt in ("status", "build", "changes", "new-version", "dev", "qa", "modules", "tier", "docs",
                "highlights"):
        p.add_argument(f"--{opt}")
    p = sub.add_parser("get"); p.add_argument("ref")
    p = sub.add_parser("list"); p.add_argument("--status"); p.add_argument("--part", choices=TABS)
    sub.add_parser("next")
    sub.add_parser("summary")
    a = ap.parse_args()
    a.xlsx = xlsx_path(a.xlsx)
    {"init": cmd_init, "set": cmd_set, "get": cmd_get, "list": cmd_list,
     "next": cmd_next, "summary": cmd_summary}[a.cmd](a)


if __name__ == "__main__":
    main()
