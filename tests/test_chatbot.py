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
        self.defaults: List[Optional[str]] = []

    def retrieve(self, question: str, default_course: Optional[str] = None) -> RetrievalResult:
        self.questions.append(question)
        self.defaults.append(default_course)
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
    assert [s.number for s in reply.sources] == [1] and "[9]" not in reply.text and "[1]" in reply.text


@pytest.mark.parametrize("text, numbers", [
    ("Fact [1-2].", [1, 2]),
    ("Fact [2; 1].", [2, 1]),
    ("Fact [ 2 ].", [2]),
    ("Fact [Passage 1].", [1]),
    ("Fact [1-999999999].", [1, 2]),
])
def test_common_citation_styles_are_understood(text: str, numbers: List[int]) -> None:
    assert [s.number for s in Chatbot(StubRetriever(), StubLLM(text)).ask("Python?").sources] == numbers


def test_several_numbers_in_one_citation_are_all_read() -> None:
    reply = Chatbot(StubRetriever(), StubLLM("Fact [2, 1].")).ask("Python?")
    assert [s.number for s in reply.sources] == [2, 1]


def test_an_answer_with_no_valid_citation_is_not_shown() -> None:
    reply = Chatbot(StubRetriever(), StubLLM("It costs nothing at all.")).ask("Python?")
    assert reply.status == "not_found" and "nothing at all" not in reply.text and reply.sources == []


def test_the_model_can_say_the_documents_do_not_have_the_answer() -> None:
    reply = Chatbot(StubRetriever(), StubLLM(NOT_FOUND_MARK)).ask("Is there a refund?")
    assert reply.status == "not_found" and NOT_FOUND_MARK not in reply.text


def test_a_good_answer_survives_a_stray_not_found_mark() -> None:
    reply = Chatbot(StubRetriever(), StubLLM(f"It takes 22 hours [1]. {NOT_FOUND_MARK}")).ask("How long?")
    assert reply.status == "answered" and NOT_FOUND_MARK not in reply.text


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


def test_the_last_course_is_offered_to_retrieval_for_the_next_question() -> None:
    retriever = StubRetriever()
    bot = Chatbot(retriever, StubLLM())
    bot.ask("How long is the Python course?")
    bot.ask("How much does it cost?")
    assert retriever.defaults == [None, PY]


def test_the_question_is_never_rewritten() -> None:
    retriever = StubRetriever()
    bot = Chatbot(retriever, StubLLM())
    bot.ask("How long is the Python course?")
    reply = bot.ask("How much does it cost?")
    assert retriever.questions[-1] == "How much does it cost?" and reply.question == "How much does it cost?"


def test_a_course_is_remembered_even_when_the_documents_had_no_answer() -> None:
    retriever = StubRetriever(route=Route(courses=[JAVA]))
    bot = Chatbot(retriever, StubLLM(NOT_FOUND_MARK))
    bot.ask("Is there a refund for the Java course?")
    bot.ask("How much does it cost?")
    assert retriever.defaults[-1] == JAVA


def test_a_question_with_no_course_does_not_forget_the_course() -> None:
    retriever = StubRetriever()
    bot = Chatbot(retriever, StubLLM())
    bot.ask("How long is the Python course?")
    retriever.result["route"] = Route(courses=[])
    bot.ask("Which courses give a certificate?")
    bot.ask("Who teaches it?")
    assert retriever.defaults[-1] == PY


def test_an_off_topic_question_does_not_change_the_course() -> None:
    retriever = StubRetriever()
    bot = Chatbot(retriever, StubLLM())
    bot.ask("How long is the Python course?")
    retriever.result.update(route=Route(courses=[JAVA]), in_scope=False, chunks=[])
    bot.ask("best beaches in java")
    bot.ask("Who teaches it?")
    assert retriever.defaults[-1] == PY


def test_after_a_comparison_no_single_course_is_assumed() -> None:
    retriever = StubRetriever(route=Route(courses=[PY, JAVA]))
    bot = Chatbot(retriever, StubLLM())
    bot.ask("Compare Python and Java")
    bot.ask("Who teaches it?")
    assert retriever.defaults[-1] is None


def test_naming_a_course_answers_which_course_do_you_mean() -> None:
    retriever = StubRetriever(needs_course=True, chunks=[])
    bot = Chatbot(retriever, StubLLM())
    bot.ask("List all sections")
    retriever.result.update(needs_course=False, chunks=[_chunk(1)])
    bot.ask("python")
    assert "List all sections" in retriever.questions[-1] and "python" in retriever.questions[-1]


def test_a_full_question_naming_a_course_is_not_the_answer_to_which_course() -> None:
    retriever = StubRetriever(needs_course=True, chunks=[])
    bot = Chatbot(retriever, StubLLM())
    bot.ask("List all sections")
    retriever.result.update(needs_course=False, chunks=[_chunk(1)])
    bot.ask("Is there a refund policy for the Python course?")
    assert retriever.questions[-1] == "Is there a refund policy for the Python course?"


def test_a_new_question_replaces_the_one_waiting_for_a_course() -> None:
    retriever = StubRetriever(needs_course=True, chunks=[])
    bot = Chatbot(retriever, StubLLM())
    bot.ask("List all sections")
    retriever.result.update(needs_course=False, chunks=[_chunk(1)])
    bot.ask("What is the weather?")
    bot.ask("python")
    assert retriever.questions[-1] == "python"


def test_the_prompt_numbers_passages_and_names_their_source() -> None:
    prompt = build_prompt("How long?", [_chunk(1), _chunk(2)])
    assert "[2]" in prompt and "passage text 2" in prompt and COURSES[PY] in prompt and "How long?" in prompt


def test_the_system_prompt_treats_passages_as_data() -> None:
    assert NOT_FOUND_MARK in SYSTEM_PROMPT and "instructions" in SYSTEM_PROMPT.lower()


def test_the_system_prompt_covers_shared_words_and_the_saved_date() -> None:
    assert "shares a word" in SYSTEM_PROMPT and "saved" in SYSTEM_PROMPT and "date" in SYSTEM_PROMPT


def test_rehearsal_flags_a_question_with_the_wrong_outcome() -> None:
    rows = rehearse(Chatbot(StubRetriever(), StubLLM()),
                    [{"q": "How long is Python?", "expect": "answered"}, {"q": "Is it free?", "expect": "not_found"}],
                    pause=0, sleep=lambda _: None)
    assert [r["ok"] for r in rows] == [True, False]


def test_rehearsal_reports_each_row_as_it_finishes() -> None:
    seen: List[str] = []
    rehearse(Chatbot(StubRetriever(), StubLLM()), [{"q": "a", "expect": "answered"}], pause=0, sleep=lambda _: None,
             on_row=lambda row: seen.append(row["q"]))
    assert seen == ["a"]


def test_rehearsal_waits_between_questions_only() -> None:
    waits: List[float] = []
    rehearse(Chatbot(StubRetriever(), StubLLM()), [{"q": "a", "expect": "answered"}, {"q": "b", "expect": "answered"}],
             pause=7, sleep=waits.append)
    assert waits == [7]
