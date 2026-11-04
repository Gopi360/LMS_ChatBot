"""Parser for saved course web pages (the layout of the five demo PDFs).

This is the *only* module that knows what a course page looks like. If the real PDFs use a
different layout, write another adapter with the same output (`ParsedCourse`) or let the
generic fallback in chunker.py handle them. The rest of the pipeline does not change.

Output sections
---------------
facts        title, subtitle, category, instructors, rating, learners, last updated, language,
             price (+ date it was captured), what the course includes, certification, length
learn / requirements / audience / description / instructor   lists and prose, as units
sections     the curriculum: section title, stated lecture count/duration, lecture titles
checks       things the ingestion report should show (info / warn)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.ingestion.layout import Unit, attach_short_headings, rows_to_units
from app.ingestion.pdf_loader import ExtractedDocument, Row
from app.ingestion.text_cleaner import (CleanStats, clean_rows, clean_title, detect_markers,
                                        match_heading, truncate_at_stop)

SECTION_HDR = re.compile(r"^(\d+)\s+lectures?\s+•\s+(.+)$")
TIME = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
SUMMARY = re.compile(r"(\d+)\s+sections?\s+•\s+(\d+)\s+lectures?\s+•\s+(.+?)\s+total length", re.I)


@dataclass
class Lecture:
    title: str
    duration: Optional[str]
    page: int


@dataclass
class Section:
    title: str
    stated_lectures: Optional[int]
    duration: Optional[str]
    page: int
    lectures: List[Lecture] = field(default_factory=list)


@dataclass
class ParsedCourse:
    title: Optional[str] = None
    facts: Dict[str, Tuple[str, int]] = field(default_factory=dict)       # key -> (value, page)
    includes: List[Tuple[str, int]] = field(default_factory=list)
    related_topics: List[Tuple[str, int]] = field(default_factory=list)
    text_regions: Dict[str, List[Unit]] = field(default_factory=dict)      # kind/heading -> units
    sections: List[Section] = field(default_factory=list)
    summary: Optional[dict] = None                                         # sections/lectures/length
    headings_found: List[str] = field(default_factory=list)
    checks: List[dict] = field(default_factory=list)
    clean_stats: Optional[CleanStats] = None
    orphan_lectures: int = 0

    def check(self, level: str, message: str) -> None:
        self.checks.append({"level": level, "message": message})


# ---------------------------------------------------------------------------------------
def is_course_page(rows: List[Row], rules: dict) -> bool:
    kinds = set()
    for r in rows:
        h = match_heading(r, rules)
        if h:
            kinds.add(h[0])
    return len(kinds - {"ignore", "stop"}) >= rules.get("udemy_min_known_headings", 3)


# ---- facts ----------------------------------------------------------------------------
def _find_cell(rows: List[Row], regex: str):
    rx = re.compile(regex)
    for r in rows:
        for c in r.cells:
            m = rx.match(c.text)
            if m:
                return r, c, m
    return None


def _extract_facts(rows: List[Row], pc: ParsedCourse) -> None:
    if not rows:
        return
    first = rows[0].page
    p1 = [r for r in rows if r.page == first]
    f = pc.facts

    # title = biggest text on the first page (wrapped titles are joined)
    big = max(p1, key=lambda r: r.size)
    if big.size >= 13:
        i = p1.index(big)
        parts = [big.text]
        j = i + 1
        while j < len(p1) and abs(p1[j].size - big.size) < 0.5:
            parts.append(p1[j].text)
            j += 1
        pc.title = " ".join(parts)
        f["title"] = (pc.title, first)
        # breadcrumb / category: small rows above the title
        for r in p1[:i]:
            if r.size <= 7.5 and not re.search(r"\d+/\d+/\d+", r.text):
                f["category"] = (" > ".join(c.text for c in r.cells), first)
                break
        # subtitle + badges: rows after the title until "Created by"
        for r in p1[j:]:
            if r.cells[0].text == "Created by":
                break
            if all(c.bold for c in r.cells) and r.size <= 7:
                f["badges"] = (", ".join(c.text for c in r.cells), first)
            elif "subtitle" not in f and r.size > 7.5:
                f["subtitle"] = (r.text, first)

    hit = _find_cell(p1, r"^Created by$")
    if hit:
        r = hit[0]
        names = [c.text.strip(" ,") for c in r.cells[1:] if c.text.strip(" ,")]
        if names:
            f["instructors"] = (", ".join(names), first)

    hit = _find_cell(p1, r"^Last updated (.+)$")
    if hit:
        r, c, m = hit
        f["last_updated"] = (m.group(1), first)
        rest = [x for x in r.cells if x.x0 > c.x0]
        if rest and re.fullmatch(r"[A-Za-z ]+", rest[0].text):
            f["language"] = (rest[0].text, first)
            caps = " ".join(x.text for x in rest[1:]).strip(" ,")
            if caps:
                f["captions"] = (re.sub(r"\s*,\s*", ", ", caps), first)

    hit = _find_cell(p1, r"^\d\.\d$")
    if hit and hit[1].size >= 10:
        f["rating"] = (hit[2].group(0), first)
    hit = _find_cell(p1, r"^([\d,]+) ratings?$")
    if hit:
        f["ratings_count"] = (hit[2].group(1), first)
    label = _find_cell(p1, r"^learners$")
    if label:
        lr, lc, _ = label
        best = None
        for r in p1:
            for c in r.cells:
                if re.fullmatch(r"[\d,]+", c.text) and 0 < lr.y - r.y < 25:
                    dx = abs((c.x0 + c.x1) / 2 - (lc.x0 + lc.x1) / 2)
                    if dx < 40 and (best is None or dx < best[0]):
                        best = (dx, c.text)
        if best:
            f["learners"] = (best[1], first)

    # price block: "Buy individual course" -> price row -> "N days left at this price!"
    for idx, r in enumerate(rows):
        if r.text.strip() == "Buy individual course":
            window = rows[idx + 1: idx + 4]
            for w in window:
                amounts = re.findall(r"([₹$€£])\s?([\d,]+(?:\.\d+)?)", w.text)
                if amounts:
                    cur = amounts[0][0]
                    value = f"{cur}{amounts[0][1]}"
                    if len(amounts) > 1:
                        value += f" (list price {cur}{amounts[1][1]}"
                        disc = re.search(r"(\d+)% off", w.text)
                        value += f", {disc.group(1)}% off)" if disc else ")"
                    f["price"] = (value, r.page)
                    break
            for w in window:
                if re.search(r"days? left at this price", w.text, re.I):
                    f["price_note"] = (w.text.strip(), r.page)
            break

    # certification the course prepares for
    for idx, r in enumerate(rows):
        if r.text.startswith("Prepare for your certification"):
            name, issuer = None, None
            for w in rows[idx + 1: idx + 5]:
                if w.text.startswith("Issued by"):
                    issuer = w.text
                elif name is None and w.text not in ("Add to interests", "Learn more"):
                    name = w.text
            if name:
                f["certification_prep"] = (name + (f" ({issuer})" if issuer else ""), r.page)
            break

    for r in rows:
        m = SUMMARY.search(r.text)
        if m:
            pc.summary = {"sections": int(m.group(1)), "lectures": int(m.group(2)), "length": m.group(3), "page": r.page}
            break


# ---- curriculum -----------------------------------------------------------------------
def _parse_curriculum(rows: List[Row], rules: dict, pc: ParsedCourse) -> None:
    cur: Optional[Section] = None
    for r in rows:
        if SUMMARY.search(r.text):
            continue
        hdr = next((c for c in r.cells if SECTION_HDR.match(c.text)), None)
        if hdr:
            m = SECTION_HDR.match(hdr.text)
            title = " ".join(c.text for c in r.cells if c is not hdr)
            cur = Section(title=clean_title(title, rules), stated_lectures=int(m.group(1)),
                          duration=m.group(2).strip(), page=r.page)
            pc.sections.append(cur)
            continue
        texts = [c.text for c in r.cells]
        if TIME.match(texts[0]):
            continue
        duration = texts[-1] if len(texts) > 1 and TIME.match(texts[-1]) else None
        if cur is None:
            pc.orphan_lectures += 1
            cur = Section(title="(Untitled section)", stated_lectures=None, duration=None, page=r.page)
            pc.sections.append(cur)
        cur.lectures.append(Lecture(title=texts[0], duration=duration, page=r.page))


# ---- main entry -----------------------------------------------------------------------
def parse_course_page(doc: ExtractedDocument, rules: dict) -> ParsedCourse:
    pc = ParsedCourse()
    raw = doc.rows
    markers = detect_markers(raw)
    stats = CleanStats()
    rows = truncate_at_stop(raw, rules, stats)

    _extract_facts(rows, pc)                      # before cleaning: needs "Buy individual course" etc.
    rows, stats = clean_rows(rows, rules, stats)
    pc.clean_stats = stats

    # split into regions at headings
    regions: List[Tuple[str, str, List[Row]]] = []
    for r in rows:
        h = match_heading(r, rules)          # only *known* headings split a course page
        if h:
            regions.append((h[0], h[1], []))
            continue
        if regions:
            regions[-1][2].append(r)
    pc.headings_found = [f"{k}:{t}" for k, t, _ in regions]

    for kind, heading, rrows in regions:
        if kind in ("ignore", "stop") or not rrows:
            continue
        if kind == "learn":
            pc.text_regions["learn"] = rows_to_units(rrows, rules, doc.page_width, two_columns=True)
        elif kind == "includes":
            pc.includes = [(c.text, r.page) for r in rrows for c in r.cells]
        elif kind == "related_topics":
            pc.related_topics = [(c.text, r.page) for r in rrows for c in r.cells]
        elif kind == "curriculum":
            _parse_curriculum(rrows, rules, pc)
        elif kind in ("requirements", "audience"):
            pc.text_regions[kind] = rows_to_units(rrows, rules, doc.page_width)
        elif kind in ("description", "instructor"):
            pc.text_regions[kind] = attach_short_headings(rows_to_units(rrows, rules, doc.page_width))
        else:
            pc.check("info", f"Heading '{heading}' has no handler; its text was skipped.")

    _validate(pc, rules, markers, stats)
    return pc


# ---- validation -----------------------------------------------------------------------
def _validate(pc: ParsedCourse, rules: dict, markers: dict, stats: CleanStats) -> None:
    kinds_found = {h.split(":", 1)[0] for h in pc.headings_found}
    for need in rules.get("udemy_required_headings", []):
        if need not in kinds_found:
            pc.check("warn", f"Expected section '{need}' was not found in the PDF.")

    if markers["show_more_pages"]:
        pc.check("warn", "The 'What you'll learn' list is collapsed in this PDF ('Show more' on page "
                         f"{markers['show_more_pages']}): items after the cut-off are not in the file. "
                         "Export the page with the list expanded to get every item.")
    if stats.truncated_at:
        pc.check("info", f"Cut at '{stats.truncated_at}' (page {stats.truncated_page}); "
                         f"{stats.rows_dropped_after_cut} rows about other courses were dropped.")

    for key in ("title", "instructors", "rating", "learners", "last_updated", "price"):
        if key not in pc.facts:
            pc.check("warn", f"Fact not found: {key}.")

    if pc.sections:
        stated_total = sum(s.stated_lectures or 0 for s in pc.sections)
        if pc.summary:
            gap = stated_total - pc.summary["lectures"]
            listed = [int(m.group(1)) for t, _ in pc.includes if (m := re.match(r"(\d+)\s", t))]
            if gap == 0:
                pass
            elif gap in listed:
                pc.check("info", f"Section headers add up to {stated_total} lectures, {gap} more than the course total ({pc.summary['lectures']}); "
                                 f"the difference equals a count under 'This course includes' ({gap}), so it is an item type counted per section but not in the total.")
            else:
                pc.check("warn", f"Section headers add up to {stated_total} lectures but the page says {pc.summary['lectures']}: a section header may have been missed.")
            if len(pc.sections) != pc.summary["sections"]:
                pc.check("warn", f"Parsed {len(pc.sections)} sections but the page says {pc.summary['sections']}.")
        else:
            pc.check("warn", "Curriculum summary line ('N sections • N lectures') not found, so section totals were not verified.")
        diff = [s for s in pc.sections if s.stated_lectures is not None and len(s.lectures) != s.stated_lectures]
        pc.check("info", f"{len(diff)} of {len(pc.sections)} sections list a different number of items than their header count; "
                         "rows without a duration (quizzes, exercises, role plays) are the usual cause. All rows found in the PDF are kept.")
        empty = [s.title for s in pc.sections if not s.lectures]
        if empty:
            pc.check("warn", f"Sections with no lectures parsed: {empty[:5]}")
        if pc.orphan_lectures:
            pc.check("warn", f"{pc.orphan_lectures} lecture rows appeared before any section header.")
    elif "curriculum" in kinds_found:
        pc.check("warn", "A 'Course content' heading was found but no curriculum sections could be parsed.")
