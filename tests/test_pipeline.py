"""Run with:  python -m tests.test_pipeline   (or pytest)

1. A synthetic PDF with a *different* layout must go through the generic chunker.
2. The five demo course PDFs (if present in Courses/) must parse with the facts we rely on.
"""
import tempfile
from pathlib import Path

import pymupdf

from app import config
from app.ingestion.pdf_loader import _printed_total
from app.ingestion.pipeline import merge_previous, process_pdf
from app.ingestion.text_cleaner import load_rules

RULES = load_rules(config.RULES_PATH)
MAX_TOKENS = config.CHUNK_MAX_TOKENS
NOISE_PHRASES = ("Students also bought", "Diwali", "Start subscription", "Personal Plan", "Boost your earning",
                 "voucher savings", "Get ahead.", "Subscribe and save", "Cancel anytime")


def _make_generic_pdf(path: Path) -> None:
    doc = pymupdf.open()
    body = ("Students must complete all modules and pass the final assessment. " * 6).strip()
    pages = [
        [("Diploma in Data Analytics", 20, True), ("Programme overview", 14, True),
         (body, 10, False), ("Eligibility", 14, True), ("Any graduate with basic maths can apply.", 10, False)],
        [("Fees and duration", 14, True), ("The programme runs for 6 months and costs Rs. 45,000.", 10, False),
         ("Assessment", 14, True), (body, 10, False)],
        [("Certification", 14, True), ("A certificate is issued after the final exam.", 10, False)],
    ]
    for n, items in enumerate(pages, 1):
        page = doc.new_page()
        page.insert_text((50, 25), "Acme Academy - Course Brochure", fontsize=8)        # running header
        page.insert_text((50, 820), f"Page {n} of 3", fontsize=8)                          # running footer
        y = 80
        for text, size, bold in items:
            font = "hebo" if bold else "helv"
            height = size * 2.4 * (1 + len(text) // 85)        # generous box so the text always fits
            page.insert_textbox(pymupdf.Rect(50, y, 540, y + height), text, fontsize=size, fontname=font)
            y += height + 12
    doc.save(path)
    doc.close()


def test_generic_layout():
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "Brochure.pdf"
        _make_generic_pdf(pdf)
        res = process_pdf(pdf, RULES, MAX_TOKENS, config.CHUNK_OVERLAP_TOKENS, write_text=False)
    assert res.status != "FAIL", res.error
    assert res.layout == "generic"
    assert res.chunks, "no chunks produced"
    sections = {c.section.split(" (part")[0] for c in res.chunks}
    assert {"Eligibility", "Fees and duration", "Certification"} <= sections, sections
    assert all(c.text.startswith("Course: Diploma in Data Analytics") for c in res.chunks)
    assert all("Acme Academy" not in c.text and "Page 2 of 3" not in c.text for c in res.chunks), "header/footer leaked"
    assert max(c.tokens_est for c in res.chunks) <= MAX_TOKENS
    fees = next(c for c in res.chunks if c.section == "Fees and duration")
    assert "6 months" in fees.body and fees.page == 2


def test_demo_courses():
    pdfs = sorted(config.COURSES_DIR.glob("*.pdf"))
    if not pdfs:
        print("  (no demo PDFs found, skipped)")
        return
    for pdf in pdfs:
        res = process_pdf(pdf, RULES, MAX_TOKENS, config.CHUNK_OVERLAP_TOKENS, write_text=False)
        assert res.status != "FAIL", f"{pdf.name}: {res.error}"
        assert res.layout == "udemy_course_page", pdf.name
        for key in ("title", "rating", "learners", "instructors", "price"):
            assert key in res.facts, f"{pdf.name}: missing fact {key}"
        assert res.curriculum["sections_parsed"] == res.curriculum["sections_stated"], pdf.name
        assert max(c.tokens_est for c in res.chunks) <= MAX_TOKENS, pdf.name
        leaked = [(p, c.chunk_id) for c in res.chunks for p in NOISE_PHRASES if p.lower() in c.text.lower()]
        assert not leaked, f"{pdf.name}: noise leaked into chunks: {leaked[:3]}"
        print(f"  ok  {pdf.name}: {len(res.chunks)} chunks")


def test_partial_run_keeps_other_courses():
    previous = {"documents": [{"pdf_name": "A.pdf"}, {"pdf_name": "B.pdf"}, {"pdf_name": "C.pdf"}],
                "chunks": [{"pdf_name": "A.pdf", "chunk_id": "a1"}, {"pdf_name": "B.pdf", "chunk_id": "b-old"},
                           {"pdf_name": "C.pdf", "chunk_id": "c1"}]}
    documents, chunks = merge_previous([{"pdf_name": "B.pdf"}], [{"pdf_name": "B.pdf", "chunk_id": "b-new"}], previous)
    assert [d["pdf_name"] for d in documents] == ["A.pdf", "B.pdf", "C.pdf"]
    assert [c["chunk_id"] for c in chunks] == ["a1", "b-new", "c1"]


def test_printed_total_pages():
    url = "https://www.example.com/course"
    assert _printed_total([f"{url} 1/21", f"{url} 2/21", f"{url} 15/21"]) == 21
    assert _printed_total(["Page 1 of 3", "Page 2 of 3"]) is None
    assert _printed_total([f"{url} 1/21", f"{url} 2/30"]) is None, "totals that disagree are not a page count"
    assert _printed_total(["Report 11/3", "Report 12/3"]) is None, "x above y is not a page number"


def test_missing_pages_are_reported():
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "Cut.pdf"
        doc = pymupdf.open()
        for n in range(1, 4):
            page = doc.new_page()
            page.insert_text((50, 100), "Some course text that is long enough to count as a page.", fontsize=10)
            page.insert_text((50, 825), f"https://www.example.com/course {n}/9", fontsize=8)
        doc.save(pdf)
        doc.close()
        res = process_pdf(pdf, RULES, MAX_TOKENS, config.CHUNK_OVERLAP_TOKENS, write_text=False)
    assert res.doc.printed_total_pages == 9
    assert res.status == "WARN"
    assert any("6 pages were left out" in c["message"] for c in res.checks), res.checks


if __name__ == "__main__":
    test_generic_layout()
    print("generic layout: ok")
    test_demo_courses()
    test_partial_run_keeps_other_courses()
    test_printed_total_pages()
    test_missing_pages_are_reported()
    print("all tests passed")
