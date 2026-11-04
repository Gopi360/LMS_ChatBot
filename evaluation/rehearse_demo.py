"""Runs the demo questions through the real chatbot, to check it before showing it to anyone.

    python -m evaluation.rehearse_demo              # waits between questions, for the free limit
    python -m evaluation.rehearse_demo --pause 30

This is a rehearsal, not a quality score: read the answers. Each question uses one Gemini request.
"""
from __future__ import annotations

import argparse
import logging
import time
from typing import Callable, Dict, List

from app.generation.chatbot import Chatbot, get_chatbot
from app.generation.llm import LLMError

DEMO_QUESTIONS: List[Dict[str, str]] = [
    {"q": "How long is the Python course?", "expect": "answered"},
    {"q": "How much does it cost?", "expect": "answered"},
    {"q": "Who runs the AWS course?", "expect": "answered"},
    {"q": "What do I need before starting the Machine Learning course?", "expect": "answered"},
    {"q": "Who is the Java course for?", "expect": "answered"},
    {"q": "What will I learn in the ethical hacking course?", "expect": "answered"},
    {"q": "Does the Python course cover decorators?", "expect": "answered"},
    {"q": "Compare the price of the Java and Python courses", "expect": "answered"},
    {"q": "List all sections", "expect": "needs_course"},
    {"q": "Is there a refund policy for the AWS course?", "expect": "not_found"},
    {"q": "how much does a ball python cost as a pet", "expect": "not_found"},
    {"q": "What is the capital of France?", "expect": "out_of_scope"},
]


def rehearse(bot: Chatbot, items: List[Dict[str, str]], pause: float,
             sleep: Callable[[float], None] = time.sleep) -> List[dict]:
    rows = []
    for position, item in enumerate(items):
        if position:
            sleep(pause)
        started = time.perf_counter()
        reply = bot.ask(item["q"])
        rows.append({"q": item["q"], "expect": item["expect"], "ok": reply.status == item["expect"],
                     "reply": reply, "seconds": time.perf_counter() - started})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pause", type=float, default=15, help="seconds between questions")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        bot = get_chatbot()
    except (LLMError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"The chatbot is not ready: {exc}")

    rows = rehearse(bot, DEMO_QUESTIONS, args.pause)
    for row in rows:
        reply = row["reply"]
        mark = "ok  " if row["ok"] else "LOOK"
        print(f"\n{mark} {row['q']}\n     expected {row['expect']}, got {reply.status}, {row['seconds']:.1f} s\n     {reply.text}")
        for s in reply.sources:
            print(f"     [{s.number}] {s.document}, page {s.page}")
    print(f"\n{sum(r['ok'] for r in rows)}/{len(rows)} behaved as expected. Read the answers too: this does not check them.")


if __name__ == "__main__":
    main()
