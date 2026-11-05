# Pipeline

## Ingestion (offline, on PDF change)

```
Courses/*.pdf
  -> app/ingestion/pdf_loader.py          read text in visual order (PyMuPDF), find printed page totals
  -> app/ingestion/text_cleaner.py        remove noise, using config/ingestion_rules.json
  -> app/ingestion/course_page_parser.py  split Udemy-style pages into typed sections
  -> app/ingestion/chunker.py             chunks up to 220 estimated tokens, with metadata
  -> data/processed/chunks.json + ingestion_report.md       (app/ingestion/pipeline.py)
  -> app/ingestion/indexer.py             embed and store in ChromaDB at vector_db/chroma
```

- Chunk types: `facts`, `requirements`, `audience`, `learn`, `curriculum`, `curriculum_overview`, `description`.
- PDFs in another layout use a generic fallback without those types, so routing widens to the whole course.
- The indexer only updates what changed. A failed PDF keeps its old chunks. `--rebuild` refuses to run if a PDF failed.
- Embeddings: `all-MiniLM-L6-v2`, CPU, 256-token limit. The largest chunk is 243 real tokens, so recheck with real PDFs.

## Answering (per question)

```
question
  -> Chatbot.ask                     app/generation/chatbot.py
     -> Retriever.retrieve           app/retrieval/retriever.py
        1. Which course?             aliases in config/retrieval_rules.json; else the remembered course, if it is a follow-up
        2. Off-topic?                score the student's own words against a cutoff
        3. What kind of question?    regex patterns first; if none fits, IntentMatcher compares with example questions
        4. Search                    ChromaDB, filtered by course and chunk type
     -> build_prompt + SYSTEM_PROMPT app/generation/prompts.py    numbered passages
     -> Generator                    app/generation/llm.py        Ollama or Gemini, chosen by LLM_PROVIDER
     -> citations checked            sources come from stored metadata, not the model's text
  -> ChatReply                       status, text, sources
```

Reply statuses: `answered`, `not_found`, `out_of_scope`, `needs_course`, `unavailable`.

## Behaviour that looks odd but is deliberate

- **Patterns always beat examples.** Alone, examples got only 62/90 right. An example match needs a score of 0.55 and a 0.03 lead,
  and a course must be clearly named, because a wrong narrow search loses the answer while a broad one usually finds it.
- **Bare names stay broad.** "Is Python free?" may be about the language, so examples are skipped unless the name has "the"
  before it or "course" after it.
- **An outline found by example also runs a normal search,** because "Break down recursion in the Java course" only sounds
  like an outline request.
- **Follow-ups never rewrite the question.** The remembered course is passed as `default_course` and only filters the search.
  Appending the title let off-topic questions through and sent Java follow-ups to the price passage, because that title
  contains "Hours".
- **A follow-up is in scope if** it scores 0.40 against course text, or 0.50 against an example, or has "it" in 5 words or fewer.
- **An answer with no valid citation is not shown.** It cannot be checked, so it becomes `not_found`.
- **A reply that stopped early is an error,** not a partial answer.

## Commands

```
.venv\Scripts\activate
python -m app.ingestion.pipeline                     # PDFs to chunks
python -m app.ingestion.indexer                      # chunks to index
python -m app.retrieval.retriever "question"         # retrieval only, no LLM
python -m app.generation.chatbot "question"          # full answer; leave the question out to chat
python -m evaluation.rehearse_demo                   # demo questions, uses the Gemini quota
python -m evaluation.evaluate_retrieval              # dev score
python -m pytest tests -q
```

Add `--patterns-only` to the evaluator to measure routing without the example questions.
