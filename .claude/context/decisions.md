# Decisions

| Decision | Why | Source |
|---|---|---|
| Demo first, real PDFs after approval | Set by the manager | Manager |
| Backend and APIs only, no chat page | The LMS already has a screen | User |
| Answer only from the PDFs | No invented fees, durations or instructors | Project rule |
| We do not train the model | Knowledge comes from search, so new PDFs need no retraining | Technical |
| Plan on a local model; no paid API is expected | Add one only if it actually arrives. Confirm with the manager | User |
| Use the lightest local model that answers correctly from a chunk | Must run on the current laptop. A stronger model is the later upgrade | User |
| Gemini free tier stays available for the demo PDFs | Works today while the local model is being proven | User |
| Real PDFs must never use the Gemini free tier | Google may use free-tier prompts, and people may read them | Project rule |
| Gemini is a stand-in, not the final plan | Free-tier data use, and it says nothing about local model quality | Technical |
| GitHub is not used to run the model | It stores code. Actions jobs are temporary, and GitHub Models was reportedly retired in July 2026 | Researched |
| Rented GPU is the fallback for a larger local model | About $0.34-0.89/hour in Nov 2026 (third-party prices), and keeps Ollama | Suggested, not chosen |
| Keep MiniLM embeddings and small chunks | `Suggession.md` proposed nomic and 500-800 tokens. That means re-tuning cutoffs and a new blind test, for no measured gain | Technical |
| `reports/` stays out of Git | The repo is public, so those files exist only on this laptop | User |
| Routing before search | Every chunk has the course name, so plain search finds the course but not the right chunk | Measured |
| Change detection uses content hashes | A rule change alters chunks while the PDF stays the same | Technical |

## Things not to redo

- Do not propose a chat page.
- Do not suggest training or fine-tuning for course knowledge.
- Do not rewrite the student's question to add a course.
- Do not replace patterns with examples.
- Do not quote 60/60, 160/174 or 83% without its context.
