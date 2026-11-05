# Decisions

| Decision | Why | Source |
|---|---|---|
| Demo first, real PDFs after approval | Set by the manager | Manager |
| Backend and APIs only, no chat page | The LMS already has a screen | User |
| Answer only from the PDFs | No invented fees, durations or instructors | Project rule |
| We do not train the model | Knowledge comes from search, so new PDFs need no retraining | Technical |
| Gemini free tier for the demo | Qwen is too slow on the laptop; free, with no new hardware | User |
| Gemini is a stand-in, not the final plan | Free-tier data use, and it says nothing about Qwen's quality | Technical |
| After the demo: paid service or Qwen on our own machine | Real PDFs must not use the free tier | **Undecided: manager** |
| GitHub is not used to run the model | It stores code. Actions jobs are temporary, and GitHub Models was reportedly retired in July 2026 | Researched |
| Rented GPU is the fallback for Qwen | About $0.34-0.89/hour in Nov 2026 (third-party prices), and keeps Ollama | Suggested, not chosen |
| `reports/` stays out of Git | The repo is public, so those files exist only on this laptop | User |
| Routing before search | Every chunk has the course name, so plain search finds the course but not the right chunk | Measured |
| Change detection uses content hashes | A rule change alters chunks while the PDF stays the same | Technical |

## Things not to redo

- Do not propose a chat page.
- Do not suggest training or fine-tuning for course knowledge.
- Do not rewrite the student's question to add a course.
- Do not replace patterns with examples.
- Do not quote 60/60, 160/174 or 83% without its context.
