# Status

Last updated: 2026-11-05. Check `git log` and `git status` too, as this file can fall behind.

## Next session: start here

1. The latest work is `feat/demo-chat-panel`, which is **local only**. Push it if the user asks. Start new branches from it.
2. The demo panel is built. Start it by double-clicking `run_demo.bat`, or see the README.
3. The user will test the panel by hand first. After that come friendly replies (asked for), then the small answer fixes, then
   a fresh known-answer test.
4. Not chosen yet, so ask before building: auto-citing one-passage answers, and the answer cache (`decisions.md`).
5. The user runs commands in **Command Prompt**, so give `set NAME=value`, not `$env:`.

## Phases

| Phase | State |
|---|---|
| 1. PDF extraction | Done |
| 2. Cleaning and chunking | Done: 304 chunks from 5 demo PDFs |
| 3. Embeddings and index | Done |
| 4. Retrieval | Done, awaiting manager approval |
| 5. LLM answers | Works with Gemini and `qwen3:1.7b`. Local answers still vary between runs (see Known gaps) |
| 6. FastAPI backend and demo chat panel | Chat, health and panel built. `/ingest`, an API key and Docker are not |
| 7. Testing and evaluation | Not started. Answer quality has no measured score yet |

## Branches

Each branch builds on the one before it. `main` is only the Phase 1-2 baseline.

| Branch | Holds | On GitHub? |
|---|---|---|
| `main` | Baseline | Yes |
| `feat/phase3-vector-index` | Cleanup, fixes, Phase 3 | Yes |
| `feat/phase4-retrieval` | Phase 4 | Yes |
| `feat/semantic-question-routing` | Example-question routing | Yes |
| `feat/gemini-answers` | Gemini answers | Yes |
| `feat/local-llm-provider` | Ollama provider | Yes |
| `fix/qwen-answer-quality` | `qwen3:1.7b` default and answer fixes | No |
| `feat/demo-chat-panel` | FastAPI and the demo chat panel. **The latest work** | No |

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
- **Qwen 4B on the current laptop:** about 1-2 minutes per reply (provisional). It has been removed from the laptop.
- **`gemma3:1b`, removed:** 0 of 12 course answers shown on a hand-picked list, because it left out citations.
- **`qwen3:1.7b`, one run after the fixes:** 13 of 14 demo questions matched, and 16 of 17 hand-picked questions. Shown answers were
  correct on the facts checked. The failing question passes in some runs and fails in others. These questions were used to design
  the fixes, so this is not a quality score. Replies took 3-20 s, long lists 35-70 s, and the first about 100 s when cold.
- **User's own test of `qwen3:1.7b`, 12 questions:** all 12 behaved as the guide predicted, including its known gaps: the Machine
  Learning answer was hidden and a label stayed visible. All 5 prices in the multi-course reply matched the PDFs. Replies took 3-14 s,
  and 36-52 s for long lists. The first model reply took 44 s.
- **Reliability through the real API, 5 tries each, fresh session:** 5 of 5 for Python length, Java length, price comparison and Java
  audience. The free laptop question was refused 5 of 5. Machine Learning requirements 4 of 5. **"Who teaches the AWS course?" only
  2 of 5:** the other 3 were hidden for having no citation, so it was removed from the example buttons. Small sample.
- **Dev set after the routing changes:** unchanged at 160/174 and 22/25. No new blind test has been run.
- **Tests:** 297 pass with `python -m pytest tests -q`.

## Current setup

- `LLM_PROVIDER` still defaults to `gemini`. Set `LLM_PROVIDER=ollama` to use the local model. `qwen3:1.7b` is installed and is the default local model.
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
- [ ] LMS team contact, and answers to the questions in `Latest_Plan.md` section 10.
- [ ] Real PDFs, or one sample file. Layout is the biggest unknown. A different layout falls back to generic chunks.

**Known gaps in the code:**
- One-passage answers are sometimes hidden for having no citation, as with "Who teaches the AWS course?". Auto-citing would fix this.
- Greetings are refused: "hi", "thanks", "bye", "who are you?" and "what can you do?" all get the off-topic reply.
- `qwen3:1.7b` sometimes copies a passage label instead of citing, so a correct answer is hidden (Machine Learning requirements).
- A label copied without its page stays visible and its number counts as a citation ("Who this course is for [1]").
- `qwen3:1.7b` drops the saved date from prices, against the prompt's rule. Adding it in code may be safer than more prompting.
- Earlier runs gave a wrong detail (739 resources, true 363) and a wrong page. Neither came back after the prompt change, but both can recur.
- A fresh known-answer test is needed. The 17 hand-picked questions are now seen and cannot prove quality.
- The rehearsal checks reply status, not facts. A 15-question known-answer test is still needed.
- Course words used as ordinary words pass retrieval ("best beaches in Java"). The prompt has to refuse them.
- Very short off-topic questions with "it" reach Gemini ("How do I cook it?").
- "Do I get a certificate?" and "Can I run it on AWS?" fail as follow-ups unless the course is named.
- Only the last course is remembered, not earlier turns.
- "List all the chapters of the Bible" gets "Which course do you mean?".
- The API replaces every `unavailable` message with one fixed reply. That hides setup details, but also useful ones such as "ask about one course".
- `LLM_PROVIDER` defaults to `gemini`, so a machine with a Gemini key and no setting sends passages to Google. Change the default
  before real PDFs arrive.
- A second review of the panel found these, not yet fixed: Retry after a "which course?" reply loses the first question; a failed
  startup needs a server restart; empty questions wait for the lock; the page hard-codes the 500-character limit.
- The API has no key yet. It listens on this laptop only. The LMS connection needs a key first.
- The latest review fixes on `fix/qwen-answer-quality` and `feat/demo-chat-panel` had no second independent review.
