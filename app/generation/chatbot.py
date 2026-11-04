"""The chatbot: retrieval finds the passages, the LLM writes the answer from them.

    python -m app.generation.chatbot "How long is the Python course?"
    python -m app.generation.chatbot            # interactive, remembers the course being discussed

The LLM is called only when there are passages to answer from. An answer must cite them, and
the sources shown come from the stored metadata of the cited passages, never from the model's
own text. An answer citing nothing cannot be checked, so it is not shown.
"""
from __future__ import annotations

import argparse
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, List, Optional

from app import config
from app.generation.llm import Generator, LLMError, get_generator
from app.generation.prompts import NOT_FOUND_MARK, SYSTEM_PROMPT, build_prompt
from app.retrieval.retriever import RetrievedChunk, find_courses, get_retriever

log = logging.getLogger(__name__)

NOT_FOUND_REPLY = "That information is not available in the provided course documents."
OUT_OF_SCOPE_REPLY = "I can only answer questions about our courses, and I could not find anything on that in the course documents."


@dataclass
class Source:
    number: int
    document: str
    course_name: str
    page: int
    page_end: int
    section: str


@dataclass
class ChatReply:
    question: str
    status: str                  # answered, not_found, out_of_scope, needs_course or unavailable
    text: str
    sources: List[Source] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _cited_numbers(text: str, passage_count: int) -> List[int]:
    """Passage numbers cited as [1] or [1, 2], in order of first use. Unknown numbers are dropped."""
    numbers: List[int] = []
    for group in re.findall(r"\[(\d+(?:\s*,\s*\d+)*)\]", text):
        for number in (int(n) for n in group.split(",")):
            if 1 <= number <= passage_count and number not in numbers:
                numbers.append(number)
    return numbers


def _source(number: int, chunk: RetrievedChunk) -> Source:
    return Source(number=number, document=chunk.document, course_name=chunk.course_name, page=chunk.page,
                  page_end=chunk.page_end, section=chunk.section)


class Chatbot:
    def __init__(self, retriever: Any, generate: Generator, max_question_chars: Optional[int] = None) -> None:
        self.retriever = retriever
        self.generate = generate
        self.max_question_chars = max_question_chars or config.MAX_QUESTION_CHARS
        self.last_courses: List[str] = []

    def _with_course(self, question: str) -> str:
        """"How much does it cost?" means the course just discussed. One course only: after a
        comparison, "it" is unclear, so the question is left as it is."""
        rules, names = self.retriever.rules, self.retriever.course_names
        pronoun = re.search(rules.get("follow_up_pattern", r"$^"), question, re.IGNORECASE)
        if not pronoun or len(self.last_courses) != 1 or find_courses(question, rules, names):
            return question
        return f"{question} (about the {names[self.last_courses[0]]} course)"

    def ask(self, question: str) -> ChatReply:
        question = question.strip()
        if not question:
            raise ValueError("The question is empty.")
        if len(question) > self.max_question_chars:
            raise ValueError(f"The question is too long. The limit is {self.max_question_chars} characters.")

        question = self._with_course(question)
        result = self.retriever.retrieve(question)
        if not result.in_scope:
            return ChatReply(question, "out_of_scope", OUT_OF_SCOPE_REPLY)
        if result.needs_course:
            names = "\n".join(f"- {name}" for name in sorted(self.retriever.course_names.values()))
            return ChatReply(question, "needs_course", f"Which course do you mean?\n{names}")
        if not result.chunks:
            return ChatReply(question, "not_found", NOT_FOUND_REPLY)

        try:
            text = self.generate(SYSTEM_PROMPT, build_prompt(question, result.chunks))
        except LLMError as exc:
            return ChatReply(question, "unavailable", str(exc))
        if NOT_FOUND_MARK in text:
            return ChatReply(question, "not_found", NOT_FOUND_REPLY)
        cited = _cited_numbers(text, len(result.chunks))
        if not cited:
            log.warning("An answer with no valid citation was not shown: %s", question)
            return ChatReply(question, "not_found", NOT_FOUND_REPLY)

        self.last_courses = result.route.courses
        return ChatReply(question, "answered", text, [_source(n, result.chunks[n - 1]) for n in cited])


def get_chatbot() -> Chatbot:
    return Chatbot(get_retriever(), get_generator())


def _show(reply: ChatReply, seconds: float) -> None:
    print(f"\n{reply.text}")
    for s in reply.sources:
        pages = f"page {s.page}" if s.page == s.page_end else f"pages {s.page}-{s.page_end}"
        print(f"  [{s.number}] {s.document}, {pages} ({s.section})")
    print(f"({reply.status}, {seconds:.1f} s)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", nargs="?", help="leave out for interactive mode")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        bot = get_chatbot()
    except (LLMError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"The chatbot is not ready: {exc}")

    def answer(question: str) -> None:
        started = time.perf_counter()
        try:
            _show(bot.ask(question), time.perf_counter() - started)
        except ValueError as exc:
            print(exc)

    if args.question:
        answer(args.question)
        return
    print("Ask about a course, or press Enter on an empty line to quit.")
    while True:
        question = input("\n> ").strip()
        if not question:
            break
        answer(question)


if __name__ == "__main__":
    main()
