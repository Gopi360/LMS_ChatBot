"""Chunking.

Purpose
-------
Turn a parsed document into retrieval-sized chunks that each stand on their own.

Strategy (course pages)
-----------------------
* 2 *facts* chunks: overview (title, instructors, rating, learners, ...) and details
  (length, what is included, price + date captured, certification).
* "What you'll learn", requirements, audience: list items packed together, never split mid-item.
* Description: paragraphs packed up to the size limit, small overlap between chunks.
* Curriculum: one chunk per section (split into parts only if the section is very long) plus
  an overview chunk listing every section, so "what topics does it cover?" can be answered.
* Every chunk starts with "Course: <title>" and "Section: <name>" so it is understandable alone.

Unknown layouts use `chunk_generic`: headings are found from font size, text is packed by
paragraph under the same size limit.

Token counts are estimates (no model download needed); the limit defaults to 220 so chunks
stay under all-MiniLM-L6-v2's 256-token input window.
"""
from __future__ import annotations

import hashlib
import re
import statistics
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Tuple

from app import config
from app.ingestion.course_page_parser import ParsedCourse
from app.ingestion.layout import Unit, attach_short_headings, rows_to_units
from app.ingestion.pdf_loader import ExtractedDocument, Row


@dataclass
class Chunk:
    chunk_id: str
    course_name: str
    pdf_name: str
    page: int
    page_end: int
    section: str
    chunk_type: str
    text: str          # what gets embedded: context header + body
    body: str          # the original content without the header
    tokens_est: int
    layout: str
    captured_on: Optional[str] = None
    source_sha1: str = ""
    content_hash: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------------------
def estimate_tokens(text: str) -> int:
    """Rough WordPiece-style estimate: words and punctuation, plus a margin for rare-word splits."""
    return int(len(re.findall(r"\w+|[^\w\s]", text)) * 1.15) + 1


def _split_long_unit(text: str, limit: int) -> List[str]:
    """A single paragraph longer than the limit: split on sentences, then on words."""
    parts, cur = [], ""
    for sent in re.split(r"(?<=[.!?])\s+", text):
        for piece in ([sent] if estimate_tokens(sent) <= limit else _split_words(sent, limit)):
            if cur and estimate_tokens(cur + " " + piece) > limit:
                parts.append(cur)
                cur = piece
            else:
                cur = (cur + " " + piece).strip()
    if cur:
        parts.append(cur)
    return parts


def _split_words(text: str, limit: int) -> List[str]:
    words, out, cur = text.split(), [], []
    for w in words:
        if cur and estimate_tokens(" ".join(cur + [w])) > limit:
            out.append(" ".join(cur))
            cur = []
        cur.append(w)
    if cur:
        out.append(" ".join(cur))
    return out


def pack(units: List[Tuple[str, int, int]], budget: int, overlap: int = 0) -> List[List[Tuple[str, int, int]]]:
    """Greedy packing of (text, page, page_end) units into groups whose estimated size is <= budget.
    With overlap > 0 the last unit(s) of a group (up to `overlap` tokens) start the next group."""
    flat: List[Tuple[str, int, int]] = []
    for text, p0, p1 in units:
        if estimate_tokens(text) > budget:
            flat.extend((piece, p0, p1) for piece in _split_long_unit(text, budget))
        else:
            flat.append((text, p0, p1))
    groups: List[List[Tuple[str, int, int]]] = []
    cur: List[Tuple[str, int, int]] = []
    used = 0
    for u in flat:
        cost = estimate_tokens(u[0]) + 1
        if cur and used + cost > budget:
            groups.append(cur)
            carry: List[Tuple[str, int, int]] = []
            if overlap:
                total = 0
                for prev in reversed(cur):
                    total += estimate_tokens(prev[0]) + 1
                    if total > overlap:
                        break
                    carry.insert(0, prev)
            cur = list(carry)
            used = sum(estimate_tokens(c[0]) + 1 for c in cur)
        cur.append(u)
        used += cost
    if cur:
        groups.append(cur)
    return groups


class _Builder:
    def __init__(self, doc: ExtractedDocument, course: str, layout: str, captured_on: Optional[str]):
        self.doc, self.course, self.layout, self.captured_on = doc, course, layout, captured_on
        self.chunks: List[Chunk] = []
        self._counter: Dict[str, int] = {}

    def add(self, chunk_type: str, section: str, body_lines: List[str], page: int, page_end: int) -> None:
        body = "\n".join(body_lines).strip()
        if not body:
            return
        header = f"Course: {self.course}\nSection: {section}\n"
        text = header + body
        n = self._counter[chunk_type] = self._counter.get(chunk_type, 0) + 1
        self.chunks.append(Chunk(
            chunk_id=f"{self.doc.slug}::{chunk_type}::{n:03d}", course_name=self.course, pdf_name=self.doc.pdf_name,
            page=page, page_end=page_end, section=section, chunk_type=chunk_type, text=text, body=body,
            tokens_est=estimate_tokens(text), layout=self.layout, captured_on=self.captured_on,
            source_sha1=self.doc.sha1, content_hash=hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]))

    def add_packed(self, chunk_type: str, section: str, units: List[Tuple[str, int, int]],
                   max_tokens: int, overlap: int = 0, bullet: str = "") -> None:
        budget = max_tokens - estimate_tokens(f"Course: {self.course}\nSection: {section} (part 99 of 99)\n")
        groups = pack(units, budget, overlap)
        for i, g in enumerate(groups, 1):
            label = section if len(groups) == 1 else f"{section} (part {i} of {len(groups)})"
            self.add(chunk_type, label, [bullet + t for t, _, _ in g], min(p for _, p, _ in g), max(p for _, _, p in g))


# ---------------------------------------------------------------------------------------
def _lectures(n: int) -> str:
    return f"{n} lecture" + ("" if n == 1 else "s")


def _iso_date(raw: Optional[str]) -> Optional[str]:
    """'11/3/26, 5:50 PM' (browser print header, month/day/year) -> '2026-11-03'."""
    if not raw:
        return None
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", raw)
    if not m:
        return None
    mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    y += 2000 if y < 100 else 0
    return f"{y:04d}-{mo:02d}-{d:02d}" if 1 <= mo <= 12 and 1 <= d <= 31 else None


def chunk_course_page(doc: ExtractedDocument, pc: ParsedCourse, max_tokens: int = None, overlap: int = None) -> List[Chunk]:
    max_tokens = max_tokens or config.CHUNK_MAX_TOKENS
    overlap = config.CHUNK_OVERLAP_TOKENS if overlap is None else overlap
    course = pc.title or doc.path.stem.replace("_", " ")
    captured = _iso_date(doc.captured_raw)
    b = _Builder(doc, course, "udemy_course_page", captured)
    F = pc.facts

    def fact(key: str, label: str, lines: List[str], pages: List[int], fmt=None):
        if key in F:
            value, page = F[key]
            lines.append(f"{label}: {fmt(value) if fmt else value}")
            pages.append(page)

    # 1) facts: overview
    lines, pages = [], []
    fact("title", "Course title", lines, pages)
    fact("subtitle", "Tagline", lines, pages)
    fact("category", "Category", lines, pages)
    fact("badges", "Badges", lines, pages)
    fact("instructors", "Instructor(s)", lines, pages)
    if "rating" in F:
        extra = f" out of 5 (based on {F['ratings_count'][0]} ratings)" if "ratings_count" in F else " out of 5"
        lines.append(f"Rating: {F['rating'][0]}{extra}")
        pages.append(F["rating"][1])
    fact("learners", "Learners enrolled", lines, pages)
    fact("last_updated", "Last updated", lines, pages)
    fact("language", "Course language", lines, pages)
    fact("captions", "Auto-generated captions", lines, pages)
    if pc.related_topics:
        lines.append("Related topics: " + ", ".join(t for t, _ in pc.related_topics))
        pages.append(pc.related_topics[0][1])
    if lines:
        b.add("facts", "Course overview", lines, min(pages), max(pages))

    # 2) facts: length, inclusions, price, certification
    lines, pages = [], []
    if pc.summary:
        lines.append(f"Total length: {pc.summary['length']} across {pc.summary['sections']} sections and {pc.summary['lectures']} lectures")
        pages.append(pc.summary["page"])
    if pc.includes:
        lines.append("This course includes: " + "; ".join(t for t, _ in pc.includes))
        pages.append(pc.includes[0][1])
    if "price" in F:
        when = f"on {captured}" if captured else "at capture time"
        note = f" (page note: \"{F['price_note'][0]}\")" if "price_note" in F else ""
        lines.append(f"Price: {F['price'][0]}. This is the price shown when the page was saved {when}{note}; prices and offers change.")
        pages.append(F["price"][1])
    fact("certification_prep", "Prepares for the certification", lines, pages)
    if lines:
        b.add("facts", "Duration, inclusions and price", lines, min(pages), max(pages))

    # 3) lists
    def as_units(us: List[Unit]) -> List[Tuple[str, int, int]]:
        return [(u.text, u.page, u.page_end) for u in us]

    for key, label, ctype in (("learn", "What you'll learn", "learn"),
                              ("requirements", "Requirements", "requirements"),
                              ("audience", "Who this course is for", "audience")):
        if pc.text_regions.get(key):
            b.add_packed(ctype, label, as_units(pc.text_regions[key]), max_tokens, overlap=0, bullet="- ")

    # 4) description / instructor bio / other prose
    for key, label in (("description", "Course description"), ("instructor", "About the instructor")):
        if pc.text_regions.get(key):
            b.add_packed(key, label, as_units(pc.text_regions[key]), max_tokens, overlap=overlap)

    # 5) curriculum
    if pc.sections:
        n = len(pc.sections)
        overview = [(f"{i}. {s.title} ({_lectures(s.stated_lectures)}, {s.duration})" if s.stated_lectures is not None
                     else f"{i}. {s.title}", s.page, s.page) for i, s in enumerate(pc.sections, 1)]
        head = f"The course has {n} sections:"
        if pc.summary:
            head = f"The course has {pc.summary['sections']} sections, {pc.summary['lectures']} lectures, {pc.summary['length']} in total:"
        b.add_packed("curriculum_overview", "Curriculum overview (list of all sections)", [(head, overview[0][1], overview[0][1])] + overview,
                     max_tokens, overlap=0)
        for i, s in enumerate(pc.sections, 1):
            if not s.lectures:
                continue
            counts = f"{_lectures(s.stated_lectures)}, {s.duration}" if s.stated_lectures is not None else "lectures"
            section = f"Curriculum section {i} of {n}: {s.title} ({counts})"
            b.add_packed("curriculum", section, [(l.title, l.page, l.page) for l in s.lectures], max_tokens, overlap=0, bullet="- ")
    return b.chunks


# ---------------------------------------------------------------------------------------
def chunk_generic(doc: ExtractedDocument, rows: List[Row], rules: dict, max_tokens: int = None, overlap: int = None) -> List[Chunk]:
    """Fallback for PDFs that do not look like a course page: headings are found from font size
    (or short bold lines), text is grouped by paragraph, then packed under the size limit."""
    max_tokens = max_tokens or config.CHUNK_MAX_TOKENS
    overlap = config.CHUNK_OVERLAP_TOKENS if overlap is None else overlap
    if not rows:
        return []
    sizes = [c.size for r in rows for c in r.cells for _ in range(max(1, len(c.text) // 20))]
    body = statistics.median(sizes)
    first_page = [r for r in rows if r.page == rows[0].page]
    big = max(first_page, key=lambda r: r.size)
    course = big.text if big.size >= body * 1.25 else doc.path.stem.replace("_", " ")

    def is_heading(r: Row) -> bool:
        t = r.text
        if len(r.cells) != 1 or len(t) > 100 or t.endswith((".", ",", ";")):
            return False
        return r.size >= body * 1.15 or (r.cells[0].bold and len(t.split()) <= 10)

    sections: List[Tuple[str, List[Row]]] = [("Introduction", [])]
    for r in rows:
        if is_heading(r) and r.text != course:
            sections.append((r.text, []))
        else:
            sections[-1][1].append(r)

    b = _Builder(doc, course, "generic", _iso_date(doc.captured_raw))
    for heading, rrows in sections:
        units = attach_short_headings(rows_to_units(rrows, rules, doc.page_width))
        if units:
            b.add_packed("section", heading, [(u.text, u.page, u.page_end) for u in units], max_tokens, overlap=overlap)
    return b.chunks
