"""Ingestion pipeline: every PDF in Courses/ -> data/processed/chunks.json + ingestion report.

    python -m app.ingestion.pipeline                 # all PDFs
    python -m app.ingestion.pipeline --only Java     # PDFs whose name contains "Java"

For each PDF:  extract (pdf_loader) -> clean + parse (text_cleaner, course_page_parser) ->
chunk (chunker) -> validate. A problem in one PDF is reported and never stops the others.
The report lists what was found, what was removed and what looks wrong, so an upload that
does not parse properly is visible immediately instead of silently giving bad answers.
"""
from __future__ import annotations

import argparse
import json
import logging
import traceback
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from app import config
from app.ingestion.chunker import Chunk, chunk_course_page, chunk_generic
from app.ingestion.course_page_parser import is_course_page, parse_course_page
from app.ingestion.pdf_loader import ExtractedDocument, extract_pdf, list_pdfs, render_text
from app.ingestion.text_cleaner import CleanStats, clean_rows, load_rules, truncate_at_stop

log = logging.getLogger("ingestion")


@dataclass
class DocResult:
    pdf_name: str
    layout: str = "unknown"
    status: str = "OK"                       # OK / WARN / FAIL
    doc: Optional[ExtractedDocument] = None
    chunks: List[Chunk] = field(default_factory=list)
    checks: List[dict] = field(default_factory=list)
    removed: Counter = field(default_factory=Counter)
    facts: dict = field(default_factory=dict)
    curriculum: Optional[dict] = None
    error: Optional[str] = None

    def check(self, level: str, message: str) -> None:
        self.checks.append({"level": level, "message": message})


def process_pdf(path: Path, rules: dict, max_tokens: int, overlap: int, write_text: bool = True) -> DocResult:
    res = DocResult(pdf_name=path.name)
    try:
        doc = extract_pdf(path, rules)
        res.doc = doc
        if write_text:
            config.EXTRACTED_TEXT_DIR.mkdir(parents=True, exist_ok=True)
            (config.EXTRACTED_TEXT_DIR / f"{path.stem}.txt").write_text(render_text(doc), encoding="utf-8")

        for e in doc.errors:
            res.check("warn", f"Extraction error, page skipped: {e}")
        if doc.printed_total_pages and doc.printed_total_pages > doc.page_count:
            res.check("warn", f"The page footers say {doc.printed_total_pages} pages but this file has {doc.page_count}: "
                              f"{doc.printed_total_pages - doc.page_count} pages were left out when the PDF was saved, so "
                              "anything on them is not available. Save the full page again to include it.")
        low = [p.number for p in doc.pages if p.low_text and not p.error]
        if low:
            res.check("warn", f"Pages with little or no extractable text: {low}. If this is a scanned PDF it needs OCR.")
        if not doc.rows:
            raise ValueError("No text could be extracted from this PDF (scanned image? it needs OCR).")

        if is_course_page(doc.rows, rules):
            pc = parse_course_page(doc, rules)
            res.layout = "udemy_course_page"
            res.chunks = chunk_course_page(doc, pc, max_tokens, overlap)
            res.checks += pc.checks
            res.removed = pc.clean_stats.cells_removed
            res.facts = {k: v for k, (v, _) in pc.facts.items()}
            res.curriculum = {
                "sections_parsed": len(pc.sections),
                "sections_stated": pc.summary["sections"] if pc.summary else None,
                "lectures_page_total": pc.summary["lectures"] if pc.summary else None,
                "lectures_in_headers": sum(s.stated_lectures or 0 for s in pc.sections),
                "lecture_rows_parsed": sum(len(s.lectures) for s in pc.sections),
            }
        else:
            res.layout = "generic"
            res.check("warn", "Layout not recognised as a course page: used the generic chunker "
                              "(headings from font size). Review the chunks, and add rules/an adapter if answers look off.")
            stats = CleanStats()
            rows = truncate_at_stop(doc.rows, rules, stats)
            rows, stats = clean_rows(rows, rules, stats)
            res.removed = stats.cells_removed
            res.chunks = chunk_generic(doc, rows, rules, max_tokens, overlap)

        # generic validation on the chunks themselves
        if not res.chunks:
            raise ValueError("The parser produced no chunks.")
        over = [c.chunk_id for c in res.chunks if c.tokens_est > max_tokens]
        if over:
            res.check("warn", f"{len(over)} chunks exceed the size limit ({max_tokens} est. tokens): {over[:3]}")
        dup = [h for h, n in Counter(c.content_hash for c in res.chunks).items() if n > 1]
        if dup:
            res.check("warn", f"{len(dup)} chunks have identical text (duplicated content).")
    except Exception as exc:  # noqa: BLE001
        res.status, res.error = "FAIL", f"{type(exc).__name__}: {exc}"
        log.error("%s failed: %s\n%s", path.name, exc, traceback.format_exc())
        return res
    res.status = "WARN" if any(c["level"] == "warn" for c in res.checks) else "OK"
    return res


# ---------------------------------------------------------------------------------------
def build_report(results: List[DocResult], max_tokens: int) -> str:
    L = ["# Ingestion report", "",
         f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  |  chunk limit: {max_tokens} est. tokens", "",
         "| PDF | Layout | Pages | Chars | Chunks | Tokens min/avg/max | Status |", "|---|---|---:|---:|---:|---|---|"]
    for r in results:
        if r.doc and r.chunks:
            t = [c.tokens_est for c in r.chunks]
            L.append(f"| {r.pdf_name} | {r.layout} | {r.doc.page_count} | {r.doc.total_chars} | {len(r.chunks)} | "
                     f"{min(t)}/{sum(t)//len(t)}/{max(t)} | {r.status} |")
        else:
            L.append(f"| {r.pdf_name} | - | - | - | 0 | - | {r.status} |")
    L.append("")
    for r in results:
        L += [f"## {r.pdf_name} — {r.status}", ""]
        if r.error:
            L += [f"**Failed:** {r.error}", ""]
            continue
        d = r.doc
        L.append(f"- Pages: {d.page_count} | extracted characters: {d.total_chars} | "
                 f"pages with little/no text: {[p.number for p in d.pages if p.low_text] or 'none'}")
        L.append(f"- Page header/footer removed (examples): {d.margin_rows_removed[:2]}")
        L.append(f"- Page saved on: {d.captured_raw or 'unknown'}")
        if r.facts:
            L.append("- Facts found: " + ", ".join(sorted(r.facts)))
        if r.curriculum:
            c = r.curriculum
            L.append(f"- Curriculum: {c['sections_parsed']} sections parsed (page says {c['sections_stated']}); "
                     f"page total {c['lectures_page_total']} lectures; section headers add up to {c['lectures_in_headers']}; "
                     f"{c['lecture_rows_parsed']} lecture rows kept")
        types = Counter(c.chunk_type for c in r.chunks)
        L.append("- Chunks by type: " + ", ".join(f"{k} {v}" for k, v in types.items()))
        if r.removed:
            top = ", ".join(f"{k} ×{v}" for k, v in r.removed.most_common(8))
            L.append(f"- Noise removed (by rule): {top}")
        for ch in r.checks:
            L.append(f"- **{ch['level'].upper()}**: {ch['message']}")
        L.append("")
    return "\n".join(L)


def merge_previous(documents: List[dict], chunks: List[dict], previous: dict) -> Tuple[List[dict], List[dict]]:
    """Keep the courses a partial run (--only) did not process, so it never drops them from
    chunks.json. Everything is returned sorted by PDF name, the order of a full run."""
    done = {d["pdf_name"] for d in documents}
    documents = documents + [d for d in previous.get("documents", []) if d["pdf_name"] not in done]
    chunks = chunks + [c for c in previous.get("chunks", []) if c["pdf_name"] not in done]
    order = {name: i for i, name in enumerate(sorted(d["pdf_name"] for d in documents))}
    return (sorted(documents, key=lambda d: order[d["pdf_name"]]),
            sorted(chunks, key=lambda c: order[c["pdf_name"]]))


def run(courses_dir: Path, only: Optional[str], max_tokens: int, overlap: int) -> List[DocResult]:
    rules = load_rules(config.RULES_PATH)
    pdfs = [p for p in list_pdfs(courses_dir) if not only or only.lower() in p.name.lower()]
    if not pdfs:
        raise SystemExit(f"No PDFs found in {courses_dir}")
    results = [process_pdf(p, rules, max_tokens, overlap) for p in pdfs]

    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    chunks_path = config.PROCESSED_DIR / "chunks.json"
    all_chunks = [c.to_dict() for r in results for c in r.chunks]
    documents = [{"pdf_name": r.pdf_name, "layout": r.layout, "status": r.status,
                  "pages": r.doc.page_count if r.doc else None, "sha1": r.doc.sha1 if r.doc else None,
                  "captured_raw": r.doc.captured_raw if r.doc else None, "chunks": len(r.chunks)} for r in results]
    if only and chunks_path.exists():
        documents, all_chunks = merge_previous(documents, all_chunks, json.loads(chunks_path.read_text(encoding="utf-8")))
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": {"max_tokens": max_tokens, "overlap_tokens": overlap, "token_counts": "estimated"},
        "documents": documents,
        "chunks": all_chunks,
    }
    chunks_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report = build_report(results, max_tokens)
    (config.PROCESSED_DIR / "ingestion_report.md").write_text(report, encoding="utf-8")
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--courses-dir", type=Path, default=config.COURSES_DIR)
    ap.add_argument("--only", help="only PDFs whose file name contains this text")
    ap.add_argument("--max-tokens", type=int, default=config.CHUNK_MAX_TOKENS)
    ap.add_argument("--overlap", type=int, default=config.CHUNK_OVERLAP_TOKENS)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    results = run(args.courses_dir, args.only, args.max_tokens, args.overlap)
    print((config.PROCESSED_DIR / "ingestion_report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
