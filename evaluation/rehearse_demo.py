"""Runs the demo questions through the real chatbot, to check it before showing it to anyone.

    python -m evaluation.rehearse_demo              # waits between questions, for the free limit
    python -m evaluation.rehearse_demo --pause 30

This is a rehearsal, not a quality score: read the answers. Each question uses one Gemini request.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Callable, Dict, List, Optional

from app.generation.chatbot import Chatbot, get_chatbot
from app.generation.llm import LLMError

DEMO_QUESTIONS: List[Dict[str, str]] = [
    {"q": "How long is the Python course?", "expect": "answered"},
    {"q": "How much does it cost?", "expect": "answered"},
    {"q": "Who runs the AWS course?", "expect": "answered"},
    {"q": "What do I need before starting the Machine Learning course?", "expect": "answered"},
    {"q": "How long is the Java course?", "expect": "answered"},
    {"q": "Who is it for?", "expect": "answered"},
    {"q": "Is it going to rain today?", "expect": "out_of_scope"},
    {"q": "What will I learn in the ethical hacking course?", "expect": "answered"},
    {"q": "Does the Python course cover decorators?", "expect": "answered"},
    {"q": "Compare the price of the Java and Python courses", "expect": "answered"},
    {"q": "List all sections", "expect": "needs_course"},
    {"q": "Is there a refund policy for the AWS course?", "expect": "answered"},
    {"q": "how much does a ball python cost as a pet", "expect": "not_found"},
    {"q": "What is the capital of France?", "expect": "out_of_scope"},
]


def rehearse(bot: Chatbot, items: List[Dict[str, str]], pause: float,
             sleep: Callable[[float], None] = time.sleep, on_row: Optional[Callable[[dict], None]] = None) -> List[dict]:
    rows = []
    for position, item in enumerate(items):
        if position:
            sleep(pause)
        started = time.perf_counter()
        reply = bot.ask(item["q"])
        rows.append({"q": item["q"], "expect": item["expect"], "ok": reply.status == item["expect"],
                     "reply": reply, "seconds": time.perf_counter() - started})
        if on_row:
            on_row(rows[-1])
    return rows


def _show(row: dict) -> None:
    reply = row["reply"]
    mark = "ok  " if row["ok"] else "LOOK"
    print(f"\n{mark} {row['q']}\n     expected {row['expect']}, got {reply.status}, {row['seconds']:.1f} s\n     {reply.text}", flush=True)
    for s in reply.sources:
        print(f"     [{s.number}] {s.document}, page {s.page}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pause", type=float, default=15, help="seconds between questions")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")     # prices use the rupee sign
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        bot = get_chatbot()
    except (LLMError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"The chatbot is not ready: {exc}")

    rows = rehearse(bot, DEMO_QUESTIONS, args.pause, on_row=_show)
    print(f"\n{sum(r['ok'] for r in rows)}/{len(rows)} behaved as expected. Read the answers too: this does not check them.")


if __name__ == "__main__":
    main()
