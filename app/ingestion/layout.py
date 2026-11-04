"""Layout helpers shared by the course-page parser and the generic fallback.

`rows_to_units` turns rows of text lines into *units* (one paragraph or one list item each).
A new unit starts when the vertical gap to the previous line is larger than normal line
spacing for that font size; a wrapped line (small gap) is glued to the unit above it.
Two-column lists ("What you'll learn") are read column by column.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from app.ingestion.pdf_loader import Row

TERMINAL = (".", "!", "?", ":", "…")


@dataclass
class Unit:
    text: str
    page: int
    page_end: int
    order: Tuple[int, float, int]


def _continues(state: dict, row: Row, cell, ratio: float, ratio_sentence: float) -> bool:
    text = cell.text
    prev_text = state["unit"].text
    if state["page"] != row.page:
        # list/paragraph crossing a page break: continue only if it clearly reads on
        return bool(text[:1].islower()) and not prev_text.endswith(TERMINAL)
    gap = cell.y - state["y"]
    threshold = ratio_sentence if prev_text.endswith(TERMINAL) else ratio
    return gap < threshold * state["size"]


def rows_to_units(rows: List[Row], rules: dict, page_width: float = 612.0, two_columns: bool = False) -> List[Unit]:
    ratio = rules.get("unit_gap_ratio", 1.75)
    ratio_sentence = rules.get("unit_gap_ratio_after_sentence", 1.5)
    split_x = page_width / 2.0
    state_by_col: dict = {}
    units: List[Unit] = []
    for r in rows:
        for c in r.cells:
            col = 1 if (two_columns and c.x0 >= split_x) else 0
            st = state_by_col.get(col)
            if st and _continues(st, r, c, ratio, ratio_sentence):
                st["unit"].text += " " + c.text
                st["unit"].page_end = r.page
            else:
                u = Unit(text=c.text, page=r.page, page_end=r.page, order=(r.page, c.y, col))
                units.append(u)
                st = {"unit": u}
                state_by_col[col] = st
            st.update(y=c.y, size=c.size, page=r.page)
    units.sort(key=lambda u: u.order)
    return units


def attach_short_headings(units: List[Unit], max_words: int = 6) -> List[Unit]:
    """A short line without final punctuation ("Why Java?", "Instructor") is a heading for the
    paragraph below it: keep them together so the heading is never separated from its text."""
    out: List[Unit] = []
    pending: List[Unit] = []
    for u in units:
        is_heading = len(u.text.split()) <= max_words and not u.text.endswith((".", ",", ";"))
        if is_heading:
            pending.append(u)
            continue
        if pending:
            u = Unit(text="\n".join([p.text for p in pending] + [u.text]), page=pending[0].page,
                     page_end=u.page_end, order=pending[0].order)
            pending = []
        out.append(u)
    out.extend(pending)  # trailing short lines stay as they are
    return out
