# LMS Course Chatbot: start here

Backend for a chatbot that answers student questions about courses, from course PDFs only. The company's LMS already exists
and keeps its own chat screen, so we deliver the backend, REST APIs and the project. **The one exception is a small demo chat
panel, requested by the user on 2026-11-05.** It is for showing and testing the bot, and does not replace the LMS screen.

Read these first. They are short and describe the current state:

@context/status.md
@context/pipeline.md
@context/workflow.md
@context/decisions.md

`Latest_Plan.md` is the full plan: phases, measured results, LMS integration and real-PDF notes. `plan.md` is older.

## Rules that must not be broken

- **Never read, print, log or commit `.env`.** It holds the Gemini key. The repo is public.
- **Never commit** `Courses/*.pdf`, `data/processed/`, `data/extracted_text/`, `vector_db/` or `reports/`. The demo PDFs are saved
  Udemy pages, so their text must not be published.
- **The Gemini free tier is for the demo PDFs only.** Google may use free-tier prompts and people may read them. Real PDFs and
  student data need a paid service or a model on our own machine.
- **Never work on `main`.** Do not push or merge unless the user asks.
- **Never tune against `held_out`.** See "Measuring" in `context/workflow.md`.
- Work one phase at a time, and wait for approval after each.
