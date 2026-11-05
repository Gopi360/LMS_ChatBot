# Project: LMS Student Course Information Chatbot — Handoff Plan (v2)

> **Read this whole file first.** Section 4 shows which phases are built; do **not** rebuild them.
> Work one phase at a time and wait for approval after each phase.

---

## 1. Project objective

Build a locally hosted AI chatbot that answers candidate questions about courses using information
extracted from course PDF documents. It will later be integrated into an existing Learning Management
System (LMS).

- **The LMS delivery is backend only.** The LMS already exists. We deliver the backend, its REST APIs and the project; the LMS
  team connects it to their system (section 10). A small demo chat panel is planned for showing and testing (Phase 6).
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
- LLM runtime: Ollama 0.40. Installed model tag: **`qwen3:1.7b`** (1.4 GB). `qwen3.5:4b` was too heavy, and `gemma3:1b` left out citations; both were removed. Re-check with `ollama list`.
- Optimise for CPU inference: small context window, no parallel LLM requests, low memory, short answers.

## 3. Technology stack

| Purpose | Choice |
|---|---|
| PDF extraction | PyMuPDF (`pymupdf`) |
| Cleaning / chunking | custom Python (already built) |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (local) |
| Vector DB | ChromaDB, persistent under `vector_db/chroma/` |
| LLM | Ollama over its HTTP API (`httpx`, no extra library); `qwen3:1.7b` |
| API | FastAPI + Uvicorn |
| RAG framework | none at first (custom Python); keep modules separable so one can be added later |

Do not add libraries without a clear need.

## 4. Status

| Phase | Status |
|---|---|
| 1. PDF inspection and extraction | **Done** (built into `pdf_loader.py`) |
| 2. Cleaning and chunking | **Done and tested** |
| 3. Embeddings + ChromaDB | **Done and tested** |
| 4. Retrieval | **Done and tested; awaiting user approval** |
| 5. LLM integration (RAG) | **Works with Gemini and with `qwen3:1.7b` for short answers; long lists are still hidden** (see `.claude/context/status.md`) |
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
      indexer.py                chunks.json -> ChromaDB; updates only what changed
    retrieval/
      embeddings.py             all-MiniLM-L6-v2 on CPU, shared by indexing and questions
      retriever.py              routes a question by course and type, then searches; has a CLI
  config/ingestion_rules.json   boilerplate patterns, known headings, thresholds
  config/retrieval_rules.json   course short names and question types
  data/extracted_text/*.txt     debug dumps with "--- PAGE n ---" (outputs, never edited by hand)
  data/processed/chunks.json    the input for the indexer
  data/processed/ingestion_report.md
  evaluation/llm_speed_test.py  measures answer time on this machine
  evaluation/evaluate_retrieval.py + retrieval_questions.json   retrieval score, dev and held-out
  tests/                        run: python -m pytest tests -q
  vector_db/chroma/             the index (generated, not in git)
  requirements.txt              pinned to the tested versions
  README.md
```

Run: `python -m app.ingestion.pipeline` (all PDFs) or `--only Java`, then `python -m app.ingestion.indexer`.
Try a question: `python -m app.retrieval.retriever "How long is the Python course?"`

### The five demo courses (Udemy course pages saved as PDF, captured 2026-11-03)

| PDF | Pages | Chunks |
|---|---:|---:|
| AWS_Certified_Cloud.pdf | 15 | 36 |
| Ethical_Hacking.pdf | 12 | 48 |
| Java_Masterclass.pdf | 33 | 92 |
| Machine_Learning.pdf | 23 | 92 |
| The_Complete_Python_Bootcamp.pdf | 11 | 36 |

Total 304 chunks. Real tokenizer: average 141, maximum 243, against the model's limit of 256, so nothing is truncated.

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
- `tokens_est` runs low: the highest real count is 243 where the highest estimate is 219. The indexer prints real counts
  on every run and warns above 256. Watch this with the real PDFs, because the margin is only 13 tokens.
- Scanned (image-only) PDFs need OCR; the report flags pages with no text.
- All 5 demo PDFs are missing their last 6–9 pages (footers say 1/21, the file has 15 pages), which is probably why there
  are no `instructor` chunks. The report now warns about this. Questions about instructor backgrounds cannot be answered.

### Measured retrieval quality (Phase 4, `evaluation/evaluate_retrieval.py`)

Latest score on a blind set: 70 course questions and 14 off-topic ones, written by a separate agent that could not see the
routing rules, the example questions or the older questions. It was run once, after the code and cutoffs were fixed. A pass
means the right chunk was retrieved, not that an answer was written.

| | Plain search | Patterns only | Patterns + example questions |
|---|---:|---:|---:|
| Course questions passed | 36/70 (51%) | 46/70 (66%) | **58/70 (83%)** |
| Off-topic questions rejected | not applicable | 11/14 | **11/14** |
| Average chunks per question | 4.0 | 3.1 | 2.7 |
| Approx. context tokens, average | 512 | 408 | 372 |
| Approx. context tokens, largest | 896 | 1,106 | 1,349 |

Example questions gained 13 and lost 1 ("will aws get me thru the CLF-C02 exam"). By question type: topic 10/10,
instructor 9/10, audience 9/10, curriculum list 8/10, duration and price 8/10, requirements 7/10, outcomes 7/10.

This set is harder than the previous ones, on purpose: about a third of it is casual chat wording. **The earlier 24/30 (80%)
must not be compared with 58/70 (83%).** The fair comparison is 46/70 to 58/70 on the same questions. This set has now been
seen, so it is spent as a test: any further tuning against it makes the 83% unfair, and the next change needs a new blind set.
The older held-out sets joined dev. Dev is 199 questions after removing near-copies of the blind set, and its score
(160/174, 22/25) is not a quality figure.

Known limits:
- **Course words used as ordinary words:** 3 of the 14 off-topic questions were accepted ("how much does a ball python cost
  as a pet", two about Java the island), because naming a course lowers the cutoff. This was not addressed by example questions.
- **Wording that still misses:** examples fire only on a strong match, so some phrasings stay broad ("whats the ml course
  going for rn"). Some "how much" questions are taken for price by a pattern ("How much RAM should my laptop have…"), and
  patterns are never overridden.
- **The full course title alone:** "Machine Learning A-Z" with no "the" or "course" is not treated as clearly naming the
  course, so such questions stay broad (at least 2 of the 12 misses). The rule exists so that "Is Python free?" stays broad.
- **An outline found by example also runs a normal search,** because a topic question can sound like an outline request.
  This protects the answer, but those are the largest contexts (up to about 1,350 tokens), which matters on a slow machine.
- **Example cutoffs (0.55 score, 0.03 lead)** were tuned on dev and on these 5 demo courses. Re-measure with the real PDFs.
- **The no-course cutoff is thin:** 0.445, with one real question at 0.448 in the final test. A cutoff set too high
  rejects real questions, so re-measure it with the real PDFs.
- **Aliases are per course:** `config/retrieval_rules.json` lists the 5 demo courses. The real PDFs need new entries, and
  every score here must be measured again on them.

### Measured LLM speed (i3-1005G1, CPU only, `evaluation/llm_speed_test.py`)

Different chunks on every run, about 1,100 prompt tokens, thinking off, model already loaded. A pip download was running
at the same time, so re-measure when the machine is idle.

| Answer length | Read prompt | Write answer | Total |
|---:|---:|---:|---:|
| 79 tokens | 41 s | 18 s | 62 s |
| 204 tokens | 42 s | 50 s | 94 s |
| 300 tokens (hit the cap) | 46 s | 69 s | 117 s |

Reading runs at about 25 tokens/s and writing at about 4.4 tokens/s. Loading a cold model adds about 19 s. Repeating an
identical prompt took 24 s only because Ollama reused its cache, so that is not a realistic figure. Free RAM was 1.4–2.2 GB
of 11.8 GB, so close other apps during a demo.

**Machine stability risk:** the development laptop restarted uncleanly 7 times between 2026-10-29 and 2026-11-04, including twice during a speed test. It also happened before this project used the LLM, so the cause is not proven. The stop codes (0x7A, 0x154) can point to paging, disk or memory faults, and Windows logged 2 bad-block errors. Do not rely on this laptop for the manager demo until that is checked.

Routing already cut the average context from about 538 to 364 tokens. Further options, to decide from Phase 5 tests: cap
answers near 120 tokens, stream the answer so the first words appear sooner, keep the model loaded (`keep_alive`), and run
on the new laptop.

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
5. No duplicates on re-index: upsert by `chunk_id`. If a course's chunk ids, `content_hash` or `source_sha1` changed, delete
   all chunks of that `pdf_name`, then add the new ones. Content hashes matter because a rule change alters chunks while the
   PDF stays the same. A PDF with status FAIL keeps its old chunks, and `--rebuild` refuses to run while one exists.
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
**Update 2026-11-05:** no paid API is expected, so a light local model on this laptop is now the plan. `OllamaGenerator` is built, and
the Gemini and Qwen 4B notes below are history. Current results are in `.claude/context/status.md`.

**Demo status:** the answer step exists in `app/generation/` (`llm.py`, `prompts.py`, `chatbot.py`), using Gemini
(`gemini-3.8-flash`) instead of Ollama, so the demo does not need new hardware. Gemini is a stand-in: its answers say nothing
about how Qwen 4B would perform, and its free tier must not receive real PDFs or student data. It is tested with stand-ins for
the model and with real SDK failures, but no real answer has been generated yet. Qwen remains to be added as a provider using
the steps below. Follow-ups use the cheap approach: a question that names no course keeps the last course as a search filter.
The question itself is never rewritten, so it is still scored in the student's own words. On a hand-written list of 21 real
follow-ups and 12 off-topic ones, 19 and 10 behaved correctly. This is a small list, not a quality score. Very short questions
with "it" reach the model even if off-topic ("How do I cook it?"), and the prompt must then reply that the documents do not say.
"Do I get a certificate?" and "Can I run it on AWS?" are rejected as follow-ups. Name the course to ask them.

1. Ollama Python client, model tag `qwen3.5:4b` from config. The model's defaults do not suit this job, so set all of
   these on every request: `think=False` (thinking is on by default), `temperature` ≈ 0.1 (default 1),
   `presence_penalty` 0 (default 1.5, which pushes the model away from repeating exact facts), `num_ctx` ≈ 3072–4096
   (the model's maximum is 262,144). One LLM request at a time (lock/queue).
   **Speed is the main constraint; see "Measured LLM speed" in section 6.** Keep the context small, cap `num_predict`
   well below 300 and stream the answer.
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
Endpoints: `POST /api/v1/chat`, `GET /api/v1/health`, `POST /api/v1/ingest`. Section 10 describes how the LMS connects.

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
- Handle errors with proper HTTP codes and logging; serialize LLM calls (one at a time).
- Use the retrieval flags: `in_scope=false` returns a fixed refusal with no LLM call (fast), and `needs_course=true` asks
  the student which course they mean.
- Optional `course` field on chat requests: when a student is on a course page, the LMS sends it and course detection is skipped.
- Deliverables: `routes.py`, `main.py`, API documentation (FastAPI docs + README section). **Stop for approval.**

### Phase 6 add-on: demo chat panel (requested 2026-11-05, design to confirm)
A small browser page for showing and testing the bot. It calls `POST /api/v1/chat`, and does not replace the LMS screen.
Students see no `[1]` markers or source list, while the citation check and the API's `sources` stay. Greetings get fixed replies,
not model text. Full requirements are in `.claude/context/workflow.md`.

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

## 10. LMS integration

We deliver a backend service. The LMS keeps its own chat screen and calls our API.

```
Student -> LMS web page -> LMS backend -> POST /api/v1/chat (with API key) -> our service -> Ollama + ChromaDB
```

**Recommended: the LMS backend calls our API, not the browser.** Pros: the API key stays secret, the LMS confirms who the
student is, and CORS is not needed. Cons: the LMS team writes one small forwarding endpoint. Direct browser calls need no
LMS backend work, but would expose the key and let anyone send a fake `student_id`.

**Delivered to the LMS team:** the code, `.env.example`, a Dockerfile, the OpenAPI spec at `/docs`, sample requests and an
integration guide.

**To confirm with the LMS team:**
1. LMS technology, and where it is hosted.
2. Where our service will run. It needs a machine for Ollama, reachable from the LMS.
3. Timeouts: answers take over a minute on weak hardware, so the LMS must wait long enough or use streaming.
4. How many students may ask at once. LLM calls run one at a time, so a busy period forms a queue.
5. Whether the LMS can send the current course with each request.
6. Who uploads new PDFs, and how.

## 11. Open decisions

1. **Re-save the 5 demo PDFs?** Recommended: yes, unless the real PDFs arrive soon. It restores the missing pages and collapsed lists.

## 12. Real PDFs later (production)

The real PDFs may not look like Udemy pages. After each upload: run the pipeline, read `ingestion_report.md`,
fix with rules or a new adapter if needed. Don't change retrieval/LLM code for layout problems.
