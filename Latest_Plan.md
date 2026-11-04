# Project: LMS Student Course Information Chatbot — Handoff Plan (v2)

> **For Claude (new conversation):** read this whole file first. Phases 1–2 are already built and tested
> (see section 4); do **not** rebuild them. The user will upload `LMS_ChatBot_phase2.zip` (the working
> project) and `ingestion_report.md`. Start by confirming the user approves Phase 2, then work on
> **Phase 3**, one phase at a time, and wait for approval after each phase.

---

## 1. Project objective

Build a locally hosted AI chatbot that answers candidate questions about courses using information
extracted from course PDF documents. It will later be integrated into an existing Learning Management
System (LMS).

- No frontend. Expose **REST API endpoints** the LMS can call.
- Use **Retrieval-Augmented Generation (RAG)**: retrieve relevant chunks from the course PDFs, generate
  the answer with a **local LLM**.
- The bot must answer **only** from the documents. No invented fees, durations, instructors,
  prerequisites or certifications. If it isn't in the documents, say so.
- Context: this is a **demo**. After the demo is approved, the real course PDFs will be provided, and
  updated PDFs will be uploaded repeatedly. So the pipeline must work **directly from PDFs** without any
  manual TXT cleaning.

## 2. Environment and hardware constraints

- OS: Windows. IDE: Antigravity. Language: Python 3.10+.
- CPU: Intel Core i3. RAM: 12 GB. GPU: Intel UHD (integrated only). **No NVIDIA GPU.**
- LLM runtime: Ollama. Installed model: Qwen3 4B. **Verify the exact tag with `ollama list`**, never assume.
- Optimise for CPU inference: small context window, no parallel LLM requests, low memory, short answers.

## 3. Technology stack

| Purpose | Choice |
|---|---|
| PDF extraction | PyMuPDF (`pymupdf`) |
| Cleaning / chunking | custom Python (already built) |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (local) |
| Vector DB | ChromaDB, persistent under `vector_db/chroma/` |
| LLM | Ollama + Qwen3 4B (Ollama Python client) |
| API | FastAPI + Uvicorn |
| RAG framework | none at first (custom Python); keep modules separable so one can be added later |

Do not add libraries without a clear need.

## 4. Status

| Phase | Status |
|---|---|
| 1. PDF inspection and extraction | **Done** (built into `pdf_loader.py`) |
| 2. Cleaning and chunking | **Done and tested; awaiting user approval** |
| 3. Embeddings + ChromaDB | **Next** |
| 4. Retrieval | to do |
| 5. LLM integration (RAG) | to do |
| 6. FastAPI backend | to do |
| 7. Testing and evaluation | to do |

### What exists in the project (`LMS_ChatBot/`)

```
LMS_ChatBot/
  Courses/                      the 5 demo PDFs (not in the zip; user keeps them)
  app/
    config.py                   paths + chunk settings (env-overridable)
    ingestion/
      pdf_loader.py             PDF -> position-ordered rows; removes headers/footers and duplicate text
      text_cleaner.py           rule-driven noise removal (config/ingestion_rules.json)
      layout.py                 groups rows into paragraph/list-item units
      course_page_parser.py     adapter for saved-course-page layout: facts, lists, curriculum + validation
      chunker.py                structure-aware chunks + generic fallback layout
      pipeline.py               runs everything -> chunks.json + ingestion_report.md
  config/ingestion_rules.json   boilerplate patterns, known headings, thresholds
  data/extracted_text/*.txt     debug dumps with "--- PAGE n ---" (outputs, never edited by hand)
  data/processed/chunks.json    THE INPUT FOR PHASE 3
  data/processed/ingestion_report.md
  tests/test_pipeline.py        run: python -m tests.test_pipeline
  requirements.txt              currently only pymupdf
  README.md
```

Run: `python -m app.ingestion.pipeline` (all PDFs) or `--only Java`.

### The five demo courses (Udemy course pages saved as PDF, captured 2026-11-03)

| PDF | Pages | Chunks |
|---|---:|---:|
| AWS_Certified_Cloud.pdf | 15 | 36 |
| Ethical_Hacking.pdf | 12 | 48 |
| Java_Masterclass.pdf | 33 | 92 |
| Machine_Learning.pdf | 23 | 92 |
| The_Complete_Python_Bootcamp.pdf | 11 | 36 |

Total 304 chunks, all ≤ ~220 estimated tokens.

### `chunks.json` format

```json
{ "generated_at": "...", "settings": {...}, "documents": [ {pdf_name, layout, status, pages, sha1, captured_raw, chunks} ],
  "chunks": [ {
    "chunk_id": "the-complete-python-bootcamp::facts::002",   // stable, deterministic
    "course_name": "The Complete Python Bootcamp From Zero to Hero in Python",
    "pdf_name": "The_Complete_Python_Bootcamp.pdf",
    "page": 2, "page_end": 2,
    "section": "Duration, inclusions and price",
    "chunk_type": "facts",
    "text": "Course: ...\nSection: ...\n<body>",   // EMBED THIS
    "body": "<original content without the header>",
    "tokens_est": 130, "layout": "udemy_course_page",
    "captured_on": "2026-11-03",                 // may be null
    "source_sha1": "...",                        // hash of the PDF file
    "content_hash": "..." } ] }
```

Chunk types: `facts` (2 per course: "Course overview" and "Duration, inclusions and price"), `learn`,
`requirements`, `audience`, `description`, `instructor` (only if the PDF has it), `curriculum_overview`
(list of all sections, split in parts for long courses, e.g. Java has 6 parts), `curriculum` (one per
section, "Curriculum section N of M: <title> (<n> lectures, <duration>)").

Example facts chunk:

```
Course: The Complete Python Bootcamp From Zero to Hero in Python
Section: Duration, inclusions and price
Total length: 22h 23m across 23 sections and 170 lectures
This course includes: 22 hours on-demand video; ...; Certificate of completion
Price: ₹459.00 (list price ₹3,199.00, 86% off). This is the price shown when the page was saved on 2026-11-03 ...
```

## 5. Decisions made (and why) — differences from the original plan

1. **PDFs are read directly**; TXT files are only debug output. Reading by position on the page fixes
   scrambled order (section headers appeared after their lectures in PyMuPDF's default order).
2. **Chunk size ≈ 220 estimated tokens max, not 500/75.** `all-MiniLM-L6-v2` only embeds the first
   256 tokens of an input; larger chunks would be silently truncated. Overlap (30 tokens) is used only for
   description prose, never for lists/curriculum. Configurable: `CHUNK_MAX_TOKENS`, `CHUNK_OVERLAP_TOKENS`.
3. **Structure-aware chunking** (facts / lists / description / curriculum per section) instead of fixed windows.
   Every chunk is prefixed with `Course:` and `Section:` so it makes sense alone and embeds the course name.
4. **Layout knowledge lives in `config/ingestion_rules.json`.** New noise = add a pattern; new heading = add it
   to `headings`. Only `course_page_parser.py` knows the course-page layout; unknown layouts fall back to a
   generic chunker (headings from font size). The real (non-Udemy) PDFs may need a new adapter.
5. **"Students also bought" and everything after it is cut** (other courses' ratings/prices would pollute answers).
6. **Prices are snapshots**: the chunk states the date the page was saved; the bot must say "as of the PDF".
7. Chunk ids are deterministic (`<pdf-slug>::<type>::<nnn>`), and each chunk carries `source_sha1` and
   `content_hash`, to support update/rebuild in Phase 3.

## 6. Known issues and caveats

- Python, Machine Learning and Ethical Hacking PDFs were saved with **"What you'll learn" collapsed ("Show more")**;
  hidden items are not in those files (report shows a WARN). For the real PDFs, export with lists expanded.
- Section lecture counts include role plays (AWS: headers sum to 296 vs page total 277); the report explains this
  as INFO. Some section titles/lecture titles are cut off by the source page itself; that is kept as is.
- Token counts are **estimates** (the embedding model was not loaded in Phase 2). Phase 3 must check real token
  counts with the model tokenizer and lower `CHUNK_MAX_TOKENS` if any chunk exceeds 256.
- Scanned (image-only) PDFs need OCR; the report flags pages with no text.
- Retrieval quality is **not yet tested**.

## 7. Remaining phases

### Phase 3: Embeddings and vector database
1. Load `all-MiniLM-L6-v2` locally (download once, then run offline). Print `model.max_seq_length`.
2. Read `data/processed/chunks.json`. Embed each chunk's `text` (header + body). Normalise embeddings;
   use cosine distance in the Chroma collection. Small batch size (e.g. 16–32) for the i3.
3. **Verify real token lengths** with the model's tokenizer; report any chunk > 256 tokens and fix chunk settings.
4. Store in ChromaDB (`PersistentClient`, path `vector_db/chroma/`): `ids=chunk_id`, `documents=text`
   (also keep `body` in metadata or a separate field), metadata = `course_name, pdf_name, page, page_end,
   section, chunk_type, captured_on, source_sha1, content_hash`. **Metadata values must be str/int/float/bool;
   drop keys whose value is null.**
5. No duplicates on re-index: upsert by `chunk_id`. Updating a PDF: if its `source_sha1` changed, delete all
   chunks of that `pdf_name`, then add the new ones (also handles chunks that no longer exist). Support a full rebuild.
6. Test: collection count equals chunks.json (304 for the demo); re-run twice and confirm count unchanged;
   change one PDF and confirm only that course is replaced.
7. Deliverables: `embeddings.py`, `indexer.py`, persistent collection, README update, a small test. **Stop for approval.**

### Phase 4: Retrieval
1. Embed the question, query Chroma, `top_k = 4` (configurable). Return document name, page, section,
   chunk_type, similarity/distance and text.
2. Course filter: optional `pdf_name`/`course_name` filter; also detect a course named in the question
   (match against known course names/short names) and filter automatically. Questions comparing courses
   need chunks from both: retrieve per course, then merge.
3. Build a CLI to inspect retrieval without the LLM.
4. **Test before LLM integration**, with a list of questions and the expected chunk, e.g.:
   duration / price / certificate -> `facts` "Duration, inclusions and price"; instructor / rating / learners /
   last updated / language -> `facts` "Course overview"; prerequisites -> `requirements`; "what will I learn" -> `learn`;
   topics/modules -> `curriculum` chunks; "does it cover X (e.g. S3, decorators)" -> matching `curriculum` chunk;
   "who is it for" -> `audience`.
5. Known risk to check: "list all topics/sections" questions on long courses (Java: curriculum overview is 6 parts)
   will not fit in top_k = 4. Options: detect "overview"-type questions and fetch all `curriculum_overview` parts of that
   course; or raise top_k for that intent. Decide from test results, not assumptions.
6. Avoid returning irrelevant chunks: consider a distance threshold and measure it on in-scope vs unrelated questions.
7. Deliverables: `retriever.py`, retrieval test script. **Stop for approval.**

### Phase 5: LLM integration and RAG
1. Ollama Python client; model tag from `ollama list`. Qwen3 4B on CPU: **turn off thinking mode**
   (use the `think=False` option if the installed Ollama supports it, otherwise `/no_think`); temperature ≈ 0.1;
   cap output length (`num_predict` ≈ 300); modest `num_ctx` (≈ 3072–4096). One LLM request at a time (lock/queue).
2. Pass only the retrieved chunks as context, each labelled with its source (document + page).
3. Use the controlled system prompt (section 8). Add: answer in clear English, concise.
4. Source references in the response are built **from the retrieved chunk metadata**, not from what the model writes.
5. Missing information: say clearly it is not in the provided course documents. Unrelated general-knowledge
   questions must be refused/redirected, not answered as course information.
6. Prices/offers: mention the date the PDF was saved.
7. Follow-up questions: keep the last few turns; make the retrieval query self-contained (cheap approach first,
   e.g. prepend the previous question or the detected course; add an LLM rewrite step only if tests show it is needed).
8. Deliverables: `llm.py`, `prompts.py`, working command-line chatbot. Test, then **stop for approval.**

### Phase 6: FastAPI backend
Endpoints: `POST /api/v1/chat`, `GET /api/v1/health`, `POST /api/v1/ingest`.

Chat request:
```json
{ "student_id": "STU101", "session_id": "SESSION001", "message": "What is the duration of the Python course?" }
```
Chat response:
```json
{ "answer": "The course duration is 22 hours and 23 minutes.",
  "sources": [ { "document": "The_Complete_Python_Bootcamp.pdf", "page": 2 } ],
  "session_id": "SESSION001" }
```
- The answer must come from retrieved PDF content, never hard-coded.
- Session memory: in-memory dict keyed by `session_id` with a max number of turns and an expiry.
- `/ingest`: **protected** (API key from environment, e.g. `X-API-Key`); accepts a PDF upload (or rescans `Courses/`),
  runs the Phase 2 pipeline, then re-indexes only changed PDFs, and returns the ingestion report summary.
  Ordinary students must not be able to modify the knowledge base.
- `/health`: check Ollama reachable, the model present, Chroma collection count.
- Handle errors with proper HTTP codes and logging; serialize LLM calls (one at a time); configurable CORS for the LMS.
- Deliverables: `routes.py`, `main.py`, API documentation (FastAPI docs + README section). **Stop for approval.**

### Phase 7: Testing and evaluation
`evaluation/test_questions.json` + `evaluate.py`. Cover: duration, requirements, instructor, curriculum, ratings,
price, course comparisons, missing information (e.g. a fee that isn't in any PDF), follow-up questions, unrelated questions.
Measure: retrieval relevance (is the expected chunk in top_k), answer correctness, groundedness, source accuracy,
response latency on the i3. Verify the bot never states unsupported facts.

## 8. System prompt (use as the base in `prompts.py`)

```
You are a Student Course Information Assistant.

Answer student questions using only the supplied course context.

Do not invent course details, fees, prerequisites, certifications, instructors or durations.

If the provided context does not contain the requested information, clearly state that the information is not available in the provided course documents.

When comparing courses, use only information available for both courses.

Provide concise and helpful responses.

Mention the source document and page number whenever possible.
```

## 9. Development rules (keep following these)

1. Work phase by phase; never generate the whole application in one step.
2. Explain the purpose of each major module briefly.
3. Keep code modular and maintainable; relative or configurable project-root paths.
4. Never modify the original PDFs; never hard-code course information.
5. No unnecessary libraries. Model/database settings in `config.py` or environment variables.
6. Test every module before moving on; keep the README updated (setup + run instructions).
7. Proper error handling and logging.
8. **Do not move to the next phase until the current one is tested and the user approves.**

## 10. Real PDFs later (production)

The real PDFs may not look like Udemy pages. After each upload: run the pipeline, read `ingestion_report.md`,
fix with rules or a new adapter if needed. Don't change retrieval/LLM code for layout problems.

---

## Starter message to paste in the new Claude conversation

Upload `Plan.md`, `LMS_ChatBot_phase2.zip` and `ingestion_report.md`, then send:

> Read Plan.md fully. Phases 1–2 are done and approved (project in the attached zip). Unzip it, run
> `python -m tests.test_pipeline` to confirm it works, then start **Phase 3 only** (embeddings + ChromaDB). Follow
> the development rules in the plan, test everything, show me the results, and wait for my approval before Phase 4.
