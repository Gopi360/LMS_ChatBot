"""Answering behaviour, with a stand-in for retrieval and the LLM, so no key or model is needed."""
from __future__ import annotations

from typing import List, Optional

import pytest

from app.generation.chatbot import Chatbot
from app.generation.llm import LLMError
from app.generation.prompts import NOT_FOUND_MARK, SYSTEM_PROMPT, build_prompt
from app.retrieval.retriever import RetrievalResult, RetrievedChunk, Route, load_rules
from evaluation.rehearse_demo import rehearse

PY, JAVA = "The_Complete_Python_Bootcamp.pdf", "Java_Masterclass.pdf"
COURSES = {PY: "The Complete Python Bootcamp", JAVA: "Java Masterclass"}


def _chunk(n: int, pdf: str = PY) -> RetrievedChunk:
    return RetrievedChunk(chunk_id=f"c{n}", document=pdf, course_name=COURSES[pdf], page=n, page_end=n + 1,
                          section=f"Section {n}", chunk_type="facts", similarity=0.7, text=f"passage text {n}")


class StubRetriever:
    def __init__(self, **result) -> None:
        self.rules = load_rules()
        self.course_names = COURSES
        self.result = result
        self.questions: List[str] = []

    def retrieve(self, question: str) -> RetrievalResult:
        self.questions.append(question)
        fields = {"route": Route(courses=[PY]), "chunks": [_chunk(1), _chunk(2)], **self.result}
        return RetrievalResult(question=question, **fields)


class StubLLM:
    def __init__(self, reply: str = "It takes 22 hours [1].", error: Optional[Exception] = None) -> None:
        self.reply = reply
        self.error = error
        self.calls: List[str] = []

    def __call__(self, system: str, prompt: str) -> str:
        self.calls.append(prompt)
        if self.error:
            raise self.error
        return self.reply


def test_an_answer_lists_only_the_sources_it_cited() -> None:
    reply = Chatbot(StubRetriever(), StubLLM("It takes 22 hours [2].")).ask("How long is Python?")
    assert reply.status == "answered"
    assert [(s.number, s.document, s.page) for s in reply.sources] == [(2, PY, 2)]


def test_a_citation_that_matches_no_passage_is_ignored() -> None:
    reply = Chatbot(StubRetriever(), StubLLM("Fact [1] and fact [9].")).ask("Python?")
    assert [s.number for s in reply.sources] == [1]


def test_several_numbers_in_one_citation_are_all_read() -> None:
    reply = Chatbot(StubRetriever(), StubLLM("Fact [2, 1].")).ask("Python?")
    assert [s.number for s in reply.sources] == [2, 1]


def test_an_answer_with_no_valid_citation_is_not_shown() -> None:
    reply = Chatbot(StubRetriever(), StubLLM("It costs nothing at all.")).ask("Python?")
    assert reply.status == "not_found" and "nothing at all" not in reply.text and reply.sources == []


def test_the_model_can_say_the_documents_do_not_have_the_answer() -> None:
    reply = Chatbot(StubRetriever(), StubLLM(NOT_FOUND_MARK)).ask("Is there a refund?")
    assert reply.status == "not_found" and NOT_FOUND_MARK not in reply.text


def test_an_off_topic_question_never_reaches_the_llm() -> None:
    llm = StubLLM()
    reply = Chatbot(StubRetriever(in_scope=False, chunks=[]), llm).ask("Capital of France?")
    assert reply.status == "out_of_scope" and llm.calls == []


def test_a_question_needing_a_course_asks_which_one() -> None:
    llm = StubLLM()
    reply = Chatbot(StubRetriever(needs_course=True, chunks=[]), llm).ask("List all sections")
    assert reply.status == "needs_course" and COURSES[JAVA] in reply.text and llm.calls == []


def test_no_passages_means_no_llm_call() -> None:
    llm = StubLLM()
    assert Chatbot(StubRetriever(chunks=[]), llm).ask("Python?").status == "not_found" and llm.calls == []


def test_an_llm_failure_becomes_a_clear_reply() -> None:
    reply = Chatbot(StubRetriever(), StubLLM(error=LLMError("The free limit is used up."))).ask("Python?")
    assert reply.status == "unavailable" and reply.text == "The free limit is used up." and reply.sources == []


@pytest.mark.parametrize("question", ["", "   ", "x" * 501])
def test_an_empty_or_oversized_question_is_an_error(question: str) -> None:
    with pytest.raises(ValueError):
        Chatbot(StubRetriever(), StubLLM(), max_question_chars=500).ask(question)


def test_a_follow_up_keeps_the_course_from_the_last_answer() -> None:
    retriever = StubRetriever()
    bot = Chatbot(retriever, StubLLM())
    bot.ask("How long is the Python course?")
    bot.ask("How much does it cost?")
    assert COURSES[PY] in retriever.questions[-1]


def test_a_new_question_without_a_pronoun_is_left_alone() -> None:
    retriever = StubRetriever()
    bot = Chatbot(retriever, StubLLM())
    bot.ask("How long is the Python course?")
    bot.ask("Which courses give a certificate?")
    assert retriever.questions[-1] == "Which courses give a certificate?"


def test_a_question_naming_a_course_is_not_treated_as_a_follow_up() -> None:
    retriever = StubRetriever()
    bot = Chatbot(retriever, StubLLM())
    bot.ask("How long is the Python course?")
    bot.ask("Who teaches it in the Java course?")
    assert retriever.questions[-1] == "Who teaches it in the Java course?"


def test_the_prompt_numbers_passages_and_names_their_source() -> None:
    prompt = build_prompt("How long?", [_chunk(1), _chunk(2)])
    assert "[2]" in prompt and "passage text 2" in prompt and COURSES[PY] in prompt and "How long?" in prompt


def test_the_system_prompt_treats_passages_as_data() -> None:
    assert NOT_FOUND_MARK in SYSTEM_PROMPT and "instructions" in SYSTEM_PROMPT.lower()


def test_rehearsal_flags_a_question_with_the_wrong_outcome() -> None:
    rows = rehearse(Chatbot(StubRetriever(), StubLLM()),
                    [{"q": "How long is Python?", "expect": "answered"}, {"q": "Is it free?", "expect": "not_found"}],
                    pause=0, sleep=lambda _: None)
    assert [r["ok"] for r in rows] == [True, False]


def test_rehearsal_waits_between_questions_only() -> None:
    waits: List[float] = []
    rehearse(Chatbot(StubRetriever(), StubLLM()), [{"q": "a", "expect": "answered"}, {"q": "b", "expect": "answered"}],
             pause=7, sleep=waits.append)
    assert waits == [7]
