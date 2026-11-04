# LMS Course Chatbot – ingestion (Phases 1–2)

Reads course PDFs **directly** and turns them into clean, retrieval-sized chunks with metadata.
No hand-made TXT files are needed: upload a new PDF, run the pipeline, read the report.

## Setup (Windows, Python 3.10+)
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```
Put the PDFs in `Courses/` (never modified).

## Run
```
python -m app.ingestion.pipeline                 # all PDFs in Courses/
python -m app.ingestion.pipeline --only Python   # one PDF
python -m app.ingestion.pdf_loader               # Phase-1 style page/character report
python -m tests.test_pipeline                    # tests
```
Outputs
* `data/processed/chunks.json` – chunks + metadata (course_name, pdf_name, page, page_end, section, chunk_type, chunk_id, captured_on, source_sha1, content_hash)
* `data/processed/ingestion_report.md` – what was found, removed and what looks wrong. **Read this after every upload.**
* `data/extracted_text/*.txt` – position-ordered text with `--- PAGE n ---` separators, for inspection only (an output, never edited by hand)

## How it works
| Module | Job |
|---|---|
| `pdf_loader.py` | PyMuPDF extraction into position-ordered rows; removes duplicate/hidden text and running headers/footers |
| `text_cleaner.py` | removes noise using `config/ingestion_rules.json`; cuts "Students also bought" (other courses' ratings/prices) |
| `course_page_parser.py` | the only code that knows the saved-course-page layout: facts, lists, description, curriculum + validation |
| `chunker.py` | structure-aware chunks; generic fallback for other layouts |
| `pipeline.py` | runs everything, writes `chunks.json` and the report; a failing PDF never stops the others |

Chunking: two facts chunks (overview; duration/inclusions/price), learn/requirements/audience lists, description paragraphs
(30-token overlap), one chunk per curriculum section plus a curriculum overview. Every chunk starts with `Course:` and `Section:`.
Max chunk size is **220 estimated tokens** because `all-MiniLM-L6-v2` only reads the first 256 tokens of an input.
Override with `CHUNK_MAX_TOKENS`, `CHUNK_OVERLAP_TOKENS` (see `app/config.py`).

## When a new PDF behaves badly
1. Open `ingestion_report.md`: WARN lines say what to check.
2. New noise (a banner, button, promo)? Add a pattern to `boilerplate_cells` in `config/ingestion_rules.json`.
3. New section heading? Add it to `headings` (`ignore`, `stop`, or a handled kind).
4. Completely different layout? The generic chunker is used automatically (report says "generic"); if answers look off, add an adapter next to `course_page_parser.py`.

## Known limits
* Token counts are estimates (the embedding model is not loaded in this phase); re-check sizes with the real tokenizer in Phase 3.
* Three demo PDFs (Python, Machine Learning, Ethical Hacking) were saved with "What you'll learn" collapsed ("Show more"); the hidden items are not in those files. Export with the list expanded.
* Scanned PDFs (images) have no text layer and need OCR; the report flags them.
* Price/offer text is a snapshot; chunks state the date the page was saved.
