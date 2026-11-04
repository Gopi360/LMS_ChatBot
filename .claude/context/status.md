# Status

Last updated: 2026-11-04. Check `git log` and `git status` too, as this file can fall behind.

## Phases

| Phase | State |
|---|---|
| 1. PDF extraction | Done |
| 2. Cleaning and chunking | Done: 304 chunks from 5 demo PDFs |
| 3. Embeddings and index | Done |
| 4. Retrieval | Done, awaiting manager approval |
| 5. LLM answers | Working for the demo with Gemini. Qwen provider not built |
| 6. FastAPI backend | Not started. Design is in `Latest_Plan.md` section 10 |
| 7. Testing and evaluation | Not started. Answer quality has no measured score yet |

## Branches

Each branch builds on the one before it. `main` is only the Phase 1-2 baseline.

| Branch | Holds | On GitHub? |
|---|---|---|
| `main` | Baseline | Yes |
| `feat/phase3-vector-index` | Cleanup, fixes, Phase 3 | Yes |
| `feat/phase4-retrieval` | Phase 4 | Yes |
| `feat/semantic-question-routing` | Example-question routing | Yes |
| `feat/gemini-answers` | Gemini answers. **The latest work** | Yes |

Repo: https://github.com/Gopi360/LMS_ChatBot (public).

## Measured results

- **Retrieval, blind test:** 58/70 course questions, against 46/70 with patterns alone and 36/70 for plain search. 11/14 off-topic
  rejected. A pass means the right passage was found, not that an answer was written.
- **The old 80% (24/30) came from an easier test.** Never compare it with 83%. The fair comparison is 46/70 to 58/70.
- **That blind set is spent.** Any further routing change needs a new one.
- **Dev set:** 160/174 and 22/25. Used for tuning, so it is not a quality figure.
- **Demo rehearsal with real Gemini:** 13 of 14 behaved as expected. The miss was a bug, since fixed, and the refund question's
  expected result was wrong. The full 14 has not been rerun since. Most replies took about 2-14 s, the slowest about 31 s.
- **Follow-ups, hand-written list:** 19 of 21 real and 10 of 12 off-topic behaved correctly. Small list, not a quality score.
- **Qwen 4B on the current laptop:** about 1-2 minutes per reply (provisional). This is why the demo uses Gemini.
- **Tests:** 149 pass with `python -m pytest tests -q`.

## Current setup

- Local `.env` has the Gemini key and `GEMINI_MODEL=gemini-3.6-flash`, because `gemini-3.8-flash` returned 503 (high demand).
  The code default is still `gemini-3.8-flash`.

## Open items

**User:**
- [ ] Rotate the Gemini key. The current one was pasted into a chat, so treat it as exposed. Replace it in `.env` by hand.
- [ ] Show the demo to the manager.
- [ ] Update `reports/` (deck and report): they still say 80% and that Phase 5 waits for a new laptop.
- [ ] Run disk and memory checks: the laptop restarted unexpectedly 7 times, cause not proven.

**Waiting on others:**
- [ ] Manager approval of Phase 4, then merge to `main`.
- [ ] New laptop or a chosen server, for Qwen.
- [ ] LMS team contact, and answers to the questions in `Latest_Plan.md` section 10.
- [ ] Real PDFs, or one sample file. Layout is the biggest unknown. A different layout falls back to generic chunks.

**Known gaps in the code:**
- Course words used as ordinary words pass retrieval ("best beaches in Java"). The prompt has to refuse them.
- Very short off-topic questions with "it" reach Gemini ("How do I cook it?").
- "Do I get a certificate?" and "Can I run it on AWS?" fail as follow-ups unless the course is named.
- Only the last course is remembered, not earlier turns.
- "List all the chapters of the Bible" gets "Which course do you mean?".
- Students could see setup messages such as "Check GEMINI_API_KEY". Split these before Phase 6.
- `last_courses` lives on one `Chatbot` object. Phase 6 needs one per student session.
- The last round of review fixes had no second independent review.
