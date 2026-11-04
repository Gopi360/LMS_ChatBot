# LMS Course Chatbot – ingestion, index and retrieval (Phases 1–4)

Reads course PDFs **directly**, turns them into clean, retrieval-sized chunks with metadata, and stores them in a local
ChromaDB index. No hand-made TXT files are needed: add a PDF, run the pipeline, read the report, run the indexer.
Retrieval finds the right chunks for a question. Writing answers (LLM) and the REST API are not built yet; see `Latest_Plan.md`.
This project is a backend only: the existing LMS provides the chat screen.

## Setup (Windows, Python 3.10+)
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```
Put the PDFs in `Courses/` (never modified, and not in git). Optional settings are listed in `.env.example`.
The first indexer run downloads the embedding model and needs internet; after that it runs offline.

## Run
```
python -m app.ingestion.pipeline                 # all PDFs in Courses/
python -m app.ingestion.pipeline --only Python   # one PDF; the other courses stay in chunks.json
python -m app.ingestion.pdf_loader               # Phase-1 style page/character report
python -m app.ingestion.indexer                  # update the index: only what changed
python -m app.ingestion.indexer --rebuild        # delete the index and build it again
python -m app.retrieval.retriever "How long is the Python course?"   # see what retrieval finds
python -m app.retrieval.retriever                # interactive
python -m evaluation.evaluate_retrieval          # retrieval score on the dev questions
python -m evaluation.llm_speed_test              # measure answer time (needs Ollama running)
python -m pytest tests -q                        # tests
```
Outputs
* `data/processed/chunks.json` – chunks + metadata (course_name, pdf_name, page, page_end, section, chunk_type, chunk_id, captured_on, source_sha1, content_hash)
* `data/processed/ingestion_report.md` – what was found, removed and what looks wrong. **Read this after every upload.**
* `vector_db/chroma/` – the search index, generated and not in git
* `data/extracted_text/*.txt` – position-ordered text with `--- PAGE n ---` separators, for inspection only (an output, never edited by hand)

## How it works
| Module | Job |
|---|---|
| `pdf_loader.py` | PyMuPDF extraction into position-ordered rows; removes duplicate/hidden text and running headers/footers |
| `text_cleaner.py` | removes noise using `config/ingestion_rules.json`; cuts "Students also bought" (other courses' ratings/prices) |
| `course_page_parser.py` | the only code that knows the saved-course-page layout: facts, lists, description, curriculum + validation |
| `chunker.py` | structure-aware chunks; generic fallback for other layouts |
| `pipeline.py` | runs everything, writes `chunks.json` and the report; a failing PDF never stops the others |
| `indexer.py` | loads chunks into ChromaDB; skips unchanged courses and keeps the old chunks of a PDF that failed |
| `retrieval/embeddings.py` | all-MiniLM-L6-v2 on the CPU, used for both chunks and questions |
| `retrieval/retriever.py` | finds the course and question type, then searches only there; rejects off-topic questions |

Chunking: two facts chunks (overview; duration/inclusions/price), learn/requirements/audience lists, description paragraphs
(30-token overlap), one chunk per curriculum section plus a curriculum overview. Every chunk starts with `Course:` and `Section:`.
Max chunk size is **220 estimated tokens** because `all-MiniLM-L6-v2` only reads the first 256 tokens of an input.
Override with `CHUNK_MAX_TOKENS`, `CHUNK_OVERLAP_TOKENS` (see `app/config.py`).

## Retrieval rules
`config/retrieval_rules.json` holds course short names and question types. **When new PDFs arrive, update `courses`**, then
re-measure: `python -m evaluation.evaluate_retrieval --scores`. Tune against `dev` only. Keep `held_out` for the final score.

Each question type has `examples` in the same file. Patterns decide first. If none fits and a course is clearly named, the
question is matched to the examples by meaning. Add examples for wordings that are missed, and keep them general.

Latest blind-test result: 58/70 course questions (patterns only: 46/70, plain search: 36/70) and 11/14 off-topic questions
rejected. That set has been used once, so write a new one before measuring another change. See `Latest_Plan.md` section 6.

## When a new PDF behaves badly
1. Open `ingestion_report.md`: WARN lines say what to check.
2. New noise (a banner, button, promo)? Add a pattern to `boilerplate_cells` in `config/ingestion_rules.json`.
3. New section heading? Add it to `headings` (`ignore`, `stop`, or a handled kind).
4. Completely different layout? The generic chunker is used automatically (report says "generic"); if answers look off, add an adapter next to `course_page_parser.py`.

## Known limits
* `tokens_est` in the chunks is an estimate and runs low. The indexer prints real counts and warns above the model's limit of 256 (the demo maximum is 243).
* All 5 demo PDFs are missing their last pages; the report shows how many. Save the full page to include them.
* Retrieval depends on recognised wording. Unusual phrasing still reaches the right course but may miss the right chunk.
* A course removed from `Courses/` stays in the index until `--rebuild`; the indexer warns about it.
* Answers took about 1–2 minutes in a first test on the development laptop (i3, CPU only); see `Latest_Plan.md` section 6.
* Three demo PDFs (Python, Machine Learning, Ethical Hacking) were saved with "What you'll learn" collapsed ("Show more"); the hidden items are not in those files. Export with the list expanded.
* Scanned PDFs (images) have no text layer and need OCR; the report flags them.
* Price/offer text is a snapshot; chunks state the date the page was saved.
