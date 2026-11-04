"""PDF loading.

Purpose
-------
Read a course PDF *directly* (no hand-made TXT) and turn every page into **rows of cells**
ordered by their position on the page. This matters for saved web pages: PyMuPDF's default
reading order follows the page's internal drawing order, which puts section headers after the
lectures they belong to and merges two-column lists. Position-based rows fix that.

What this module does
---------------------
* extracts text with font size / bold information and bounding boxes,
* drops stacked duplicate text (the same line drawn twice at the same position),
* groups lines that share a baseline into one row (title + "Preview" + "4:54" -> one row),
* removes running headers/footers (lines repeated on most pages inside the page margins),
* reports page count, character count and pages with little/no text,
* never modifies the PDF and never raises for a single bad page.

Run standalone for the Phase-1 style report:  python -m app.ingestion.pdf_loader
"""
from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import pymupdf  # PyMuPDF

from app import config

log = logging.getLogger(__name__)


@dataclass
class Cell:
    text: str
    x0: float
    x1: float
    size: float
    bold: bool
    y: float = 0.0     # vertical centre of this particular line


@dataclass
class Row:
    page: int          # 1-based page number
    y: float           # vertical centre of the row, in PDF points from the top
    cells: List[Cell]

    @property
    def text(self) -> str:
        return " ".join(c.text for c in self.cells)

    @property
    def size(self) -> float:
        return max(c.size for c in self.cells)


@dataclass
class PageInfo:
    number: int
    chars: int = 0
    rows: int = 0
    low_text: bool = False
    error: Optional[str] = None


@dataclass
class ExtractedDocument:
    path: Path
    pdf_name: str
    slug: str
    sha1: str
    page_count: int
    page_width: float
    pages: List[PageInfo] = field(default_factory=list)
    rows: List[Row] = field(default_factory=list)          # header/footer already removed
    margin_rows_removed: List[str] = field(default_factory=list)
    captured_raw: Optional[str] = None                      # e.g. "11/3/26, 5:50 PM" from the page header
    printed_total_pages: Optional[int] = None               # the "y" of an "x/y" page footer, if any
    errors: List[str] = field(default_factory=list)

    @property
    def total_chars(self) -> int:
        return sum(p.chars for p in self.pages)


# ----------------------------------------------------------------------------------------
def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _is_bold(span: dict) -> bool:
    font = span.get("font", "")
    return bool(span.get("flags", 0) & 16) or any(k in font for k in ("Bold", "Semibold", "Black", "Heavy"))


def _extract_lines(page: "pymupdf.Page") -> List[dict]:
    """All text lines on a page with bbox/size/bold. Exact duplicates (same position and text,
    e.g. a visible and a hidden copy drawn on top of each other) are kept once."""
    rect = page.rect
    seen, lines = set(), []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            text = "".join(s["text"] for s in line["spans"])
            text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)          # zero-width characters
            text = re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()
            if not text:
                continue
            x0, y0, x1, y1 = line["bbox"]
            if x1 < rect.x0 or x0 > rect.x1 or y1 < rect.y0 or y0 > rect.y1:
                continue  # entirely outside the page (hidden text)
            key = (round(x0), round(y0), text)
            if key in seen:
                continue
            seen.add(key)
            spans = [s for s in line["spans"] if s["text"].strip()]
            lines.append({
                "x0": x0, "x1": x1, "y0": y0, "y1": y1, "text": text,
                "size": max(s["size"] for s in spans),
                "bold": any(_is_bold(s) for s in spans),
            })
    return lines


def _cluster_rows(lines: List[dict], page_no: int, tol: float) -> List[Row]:
    rows: List[dict] = []
    for ln in sorted(lines, key=lambda l: (l["y0"] + l["y1"]) / 2):
        yc = (ln["y0"] + ln["y1"]) / 2
        if rows and abs(rows[-1]["y"] - yc) <= tol:
            rows[-1]["lines"].append(ln)
        else:
            rows.append({"y": yc, "lines": [ln]})
    out = []
    for r in rows:
        cells = [Cell(l["text"], l["x0"], l["x1"], l["size"], l["bold"], (l["y0"] + l["y1"]) / 2)
                 for l in sorted(r["lines"], key=lambda l: l["x0"])]
        out.append(Row(page=page_no, y=r["y"], cells=cells))
    return out


def _signature_text(text: str) -> str:
    return re.sub(r"\d+", "#", text)


def _signature(row: Row) -> str:
    return _signature_text(row.text)


def _printed_total(footers: List[str]) -> Optional[int]:
    """Total page count printed in running footers such as "https://... 3/21". Browsers print
    the total of the whole web page, so a total above the file's page count means pages were
    left out when the PDF was saved. The total must agree on every footer, which keeps a stray
    fraction or date from being read as a page count."""
    totals = set()
    for text in footers:
        m = re.search(r"(?:^|\s)(\d{1,4})/(\d{1,4})$", text.strip())
        if not m or int(m.group(1)) > int(m.group(2)):
            return None
        totals.add(int(m.group(2)))
    return totals.pop() if len(totals) == 1 else None


def _strip_margin_rows(rows_by_page: dict, heights: dict, cfg: dict) -> tuple[dict, List[str], Optional[str], Optional[int]]:
    """Remove running headers/footers: rows inside the top/bottom margin whose text (digits
    ignored) repeats on most pages. Returns (rows_by_page, removed_samples, captured_raw,
    printed_total_pages)."""
    header_pt, footer_pt = cfg.get("header_pt", 35), cfg.get("footer_pt", 35)
    ratio, min_pages = cfg.get("repeat_ratio", 0.6), cfg.get("min_pages", 3)

    def in_margin(r: Row) -> bool:
        return r.y < header_pt or r.y > heights[r.page] - footer_pt

    n_pages = len(rows_by_page)
    counts: Counter = Counter()
    for rows in rows_by_page.values():
        counts.update({_signature(r) for r in rows if in_margin(r)})

    removed: List[str] = []
    footers: List[str] = []
    captured = None
    if n_pages < min_pages:
        return rows_by_page, removed, captured, None
    cleaned = {}
    for page_no, rows in rows_by_page.items():
        keep = []
        for r in rows:
            if in_margin(r) and counts[_signature(r)] >= ratio * n_pages:
                if len(removed) < 6 and _signature(r) not in {_signature_text(x) for x in removed}:
                    removed.append(r.text)
                if r.y > heights[r.page] - footer_pt:
                    footers.append(r.text)
                if captured is None:
                    m = re.search(r"\d{1,2}/\d{1,2}/\d{2,4},\s*\d{1,2}:\d{2}\s*[AP]M", r.text)
                    captured = m.group(0) if m else None
                continue
            keep.append(r)
        cleaned[page_no] = keep
    return cleaned, removed, captured, _printed_total(footers) if footers else None


# ----------------------------------------------------------------------------------------
def extract_pdf(path: Path, rules: dict) -> ExtractedDocument:
    """Extract one PDF. Page-level failures are recorded in `errors`, not raised."""
    path = Path(path)
    data = path.read_bytes()
    doc = pymupdf.open(stream=data, filetype="pdf")
    result = ExtractedDocument(
        path=path, pdf_name=path.name, slug=_slugify(path.stem),
        sha1=hashlib.sha1(data).hexdigest(), page_count=len(doc),
        page_width=doc[0].rect.width if len(doc) else 0.0,
    )
    rows_by_page, heights = {}, {}
    for i, page in enumerate(doc, start=1):
        info = PageInfo(number=i)
        try:
            lines = _extract_lines(page)
            rows_by_page[i] = _cluster_rows(lines, i, rules.get("row_tolerance_pt", 3.0))
            heights[i] = page.rect.height
        except Exception as exc:  # noqa: BLE001 - keep going, report it
            info.error = f"{type(exc).__name__}: {exc}"
            result.errors.append(f"page {i}: {info.error}")
            log.warning("%s page %d failed: %s", path.name, i, exc)
            rows_by_page[i], heights[i] = [], page.rect.height
        result.pages.append(info)
    doc.close()

    rows_by_page, removed, captured, printed_total = _strip_margin_rows(rows_by_page, heights, rules.get("page_margins", {}))
    result.margin_rows_removed, result.captured_raw, result.printed_total_pages = removed, captured, printed_total

    for info in result.pages:
        rows = rows_by_page[info.number]
        info.rows = len(rows)
        info.chars = sum(len(r.text) for r in rows)
        info.low_text = info.chars < config.LOW_TEXT_CHAR_THRESHOLD
        result.rows.extend(rows)
    return result


def render_text(doc: ExtractedDocument) -> str:
    """Human-readable dump (page separators kept) for inspection/debugging. It is an *output*
    of the pipeline, never an input you have to edit by hand."""
    out = [f"DOCUMENT: {doc.pdf_name}", f"TOTAL PAGES: {doc.page_count}", "=" * 70, ""]
    by_page: dict = {}
    for r in doc.rows:
        by_page.setdefault(r.page, []).append(r)
    for info in doc.pages:
        out.append(f"--- PAGE {info.number} ---")
        out.extend(" | ".join(c.text for c in r.cells) for r in by_page.get(info.number, []))
        out.append("")
    return "\n".join(out)


def list_pdfs(courses_dir: Path) -> List[Path]:
    return sorted(Path(courses_dir).glob("*.pdf"))


if __name__ == "__main__":  # Phase-1 style report
    import json
    logging.basicConfig(level=logging.INFO)
    rules = json.loads(config.RULES_PATH.read_text(encoding="utf-8"))
    print(f"{'document':36} {'pages':>5} {'chars':>8}  low-text pages")
    for pdf in list_pdfs(config.COURSES_DIR):
        d = extract_pdf(pdf, rules)
        low = [p.number for p in d.pages if p.low_text]
        print(f"{d.pdf_name:36} {d.page_count:>5} {d.total_chars:>8}  {low or '-'}")
