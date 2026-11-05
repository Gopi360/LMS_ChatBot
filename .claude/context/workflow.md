# Workflow

## How the user likes to work

- The user is Supriya Gope (Gopi), an AI and automation developer. Explain in plain English: they relay this to a manager.
- Always give pros and cons when suggesting an approach.
- When asked, use the superpowers skills and subagents on the best available model.
- Commit at natural checkpoints on a feature branch, using conventional commits. Push or merge only when asked.
- Keep the README updated.

## Building a change

1. Start a new branch from the latest branch, because `main` is behind until Phase 4 is approved.
2. Write the test, and watch it fail for the right reason.
3. Write the code, then run the whole suite.
4. **Try it on the real index, not only the fake one.** Fake embeddings hid real bugs here: "List all sections" was rejected,
   and short follow-ups were treated as off-topic.
5. Get an independent code review before calling it finished. Both reviews so far found serious bugs.
6. If a key is available, run `python -m evaluation.rehearse_demo` and read the answers.

Windows notes: shell heredocs can break on quotes or `\b`. Write Python edits to a scratch file with the Write tool instead.
In PowerShell 5.1, `2>$null` on a native command can make `$?` false, so do not chain on it.

## Measuring

The evaluator is `evaluation/evaluate_retrieval.py`, with questions in `evaluation/retrieval_questions.json`.

- **Tune only on `dev`.** Run `held_out` once, after the code and settings are final.
- **Failures you have seen cannot prove a fix.** Move them into `dev`, and test on new questions.
- **A new `held_out` must be written by a separate agent** that cannot see the rules, the examples or the older questions. Give it
  only `data/processed/chunks.json`.
- Remove anything from `dev` that is a near-copy of `held_out` (cosine 0.95 or more).
- Run a baseline on the same questions, then report the before and after together.
- Report what was lost as well as what was gained. Never claim a score from a set that was used for tuning.

## Adding things

- **New course:** add its aliases to `courses` in `config/retrieval_rules.json`, run the pipeline and indexer, then re-measure.
- **New wording that misses:** add a general example to the question type. Do not copy the test question.
- **New LLM provider:** add a class in `app/generation/llm.py`, callable with `(system, prompt)`, and select it with `LLM_PROVIDER`.
  Qwen needs `think=False`, temperature about 0.1, `presence_penalty` 0 and `num_ctx` about 3072-4096.
- **New settings:** add them to `app/config.py` with an env override, and to `.env.example`.

## Next phase: FastAPI (Phase 6)

Planned endpoints: `POST /api/v1/chat`, `GET /api/v1/health`, `POST /api/v1/ingest` (admin). The LMS backend calls our API, never the
student's browser. Read `Latest_Plan.md` section 10 first, and use the `fastapi-patterns` skill. Docker is required for production.
