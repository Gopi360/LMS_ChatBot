"""Text cleaning.

Purpose
-------
Remove page noise (sale banners, buttons, promos, "students also bought" cards) from the rows
produced by pdf_loader, **driven by config/ingestion_rules.json** instead of per-file manual edits.
When a new kind of noise appears in an uploaded PDF you add one pattern to the rules file;
the code does not change.

Everything removed is counted per rule so the ingestion report shows exactly what was dropped.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from app.ingestion.pdf_loader import Cell, Row


def load_rules(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@dataclass
class CleanStats:
    cells_removed: Counter = field(default_factory=Counter)   # rule note/pattern -> count
    rows_in: int = 0
    rows_out: int = 0
    truncated_at: Optional[str] = None
    truncated_page: Optional[int] = None
    rows_dropped_after_cut: int = 0


# ---------------------------------------------------------------------------------------
def match_heading(row: Row, rules: dict, allow_unknown: bool = False) -> Optional[Tuple[str, str]]:
    """Return (kind, text) if the row is a section heading, else None.

    A heading is a single-cell, non-bold row in a large font. Known headings map to a kind
    (learn, curriculum, stop, ...) in the rules file; unknown big-font text is returned as
    ("unknown", text) once the document body has started (allow_unknown=True)."""
    if len(row.cells) != 1:
        return None
    cell = row.cells[0]
    if cell.size < rules.get("heading_min_size", 10.5) or cell.bold:
        return None
    kind = rules.get("headings", {}).get(cell.text.strip().lower())
    if kind:
        return kind, cell.text.strip()
    if (allow_unknown and re.search(r"[A-Za-z]", cell.text) and len(cell.text) <= 80
            and not re.search(r"[₹$€£]", cell.text)):
        return "unknown", cell.text.strip()
    return None


def detect_markers(rows: List[Row]) -> dict:
    """Facts about the raw rows that must be noted *before* buttons are stripped."""
    show_more = sorted({r.page for r in rows for c in r.cells if c.text == "Show more"})
    return {"show_more_pages": show_more}


def truncate_at_stop(rows: List[Row], rules: dict, stats: CleanStats) -> List[Row]:
    """Cut everything from the first 'stop' heading (e.g. 'Students also bought') onwards:
    those cards describe *other* courses and would pollute answers with wrong ratings/prices."""
    for i, r in enumerate(rows):
        h = match_heading(r, rules)
        if h and h[0] == "stop":
            stats.truncated_at, stats.truncated_page = h[1], r.page
            stats.rows_dropped_after_cut = len(rows) - i
            return rows[:i]
    return rows


# ---------------------------------------------------------------------------------------
def _compile_boilerplate(rules: dict):
    compiled = []
    for entry in rules.get("boilerplate_cells", []):
        spec = {"pattern": entry} if isinstance(entry, str) else entry
        compiled.append((re.compile(spec["pattern"], re.IGNORECASE), spec.get("min_size"),
                         spec.get("max_size"), spec.get("note") or spec["pattern"]))
    return compiled


def clean_rows(rows: List[Row], rules: dict, stats: Optional[CleanStats] = None) -> Tuple[List[Row], CleanStats]:
    stats = stats or CleanStats()
    stats.rows_in = len(rows)
    boiler = _compile_boilerplate(rules)
    fixes = rules.get("artifact_fixes", [])
    out: List[Row] = []
    for r in rows:
        cells = [Cell(re.sub(r"\s+", " ", c.text).strip(), c.x0, c.x1, c.size, c.bold, c.y) for c in r.cells]
        cells = [c for c in cells if c.text]

        # artifact fixes: e.g. an overlapped "S" left at the end of a title next to "tart subscription"
        for fx in fixes:
            if any(c.text.lower() == fx["if_cell"].lower() for c in cells):
                pat = re.compile(fx["strip_trailing_from_other_cells"])
                for c in cells:
                    if c.text.lower() != fx["if_cell"].lower():
                        c.text = pat.sub("", c.text)
                stats.cells_removed["artifact: " + fx.get("note", fx["if_cell"])] += 1

        kept = []
        for c in cells:
            rule_note = None
            for rx, min_s, max_s, note in boiler:
                if rx.fullmatch(c.text) and (min_s is None or c.size >= min_s) and (max_s is None or c.size <= max_s):
                    rule_note = note
                    break
            if rule_note:
                stats.cells_removed[rule_note] += 1
            else:
                kept.append(c)
        if kept:
            out.append(Row(page=r.page, y=r.y, cells=kept))
    stats.rows_out = len(out)
    return out, stats


def clean_title(text: str, rules: dict) -> str:
    for rule in rules.get("title_cleanup", []):
        text = re.sub(rule["pattern"], rule["replace"], text)
    return text.strip()
