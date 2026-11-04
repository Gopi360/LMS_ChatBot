"""Routing and search behaviour, with a tiny fake index so no model download is needed."""
from __future__ import annotations

import hashlib
from typing import List

import pytest

from app.ingestion.indexer import get_collection, index_chunks, open_client
from app.retrieval.retriever import Retriever, build_filter, find_courses, load_rules, route_question

RULES = load_rules()
COURSES = {
    "AWS_Certified_Cloud.pdf": "[NEW] Ultimate AWS Certified Cloud Practitioner CLF-C02 2026",
    "Ethical_Hacking.pdf": "Learn Ethical Hacking From Scratch",
    "Java_Masterclass.pdf": "Java Masterclass 2025: 130+ Hours of Expert Lessons",
    "Machine_Learning.pdf": "Machine Learning A-Z [2026]: ML, DL, AI with AWS, Python & R",
    "The_Complete_Python_Bootcamp.pdf": "The Complete Python Bootcamp From Zero to Hero in Python",
}
AWS, HACK, JAVA, ML, PY = sorted(COURSES)


@pytest.mark.parametrize("question, expected", [
    ("How long is the Python course?", [PY]),
    ("How long is the AWS course?", [AWS]),
    ("What does the ML course cost?", [ML]),
    ("Does the Machine Learning course use Python?", [ML]),
    ("Does the Python course cover machine learning?", [PY]),
    ("Does the AWS course cover machine learning?", [AWS]),
    ("Compare the Python and Java courses", [PY, JAVA]),
    ("Java vs Python", [JAVA, PY]),
    ("Do you have a JavaScript course?", []),
    ("What is HTML?", []),
    ("Which course is the longest?", []),
])
def test_find_courses(question: str, expected: List[str]) -> None:
    assert find_courses(question, RULES, COURSES) == expected


def test_full_title_works_for_a_course_with_no_rules_entry() -> None:
    courses = {"New.pdf": "Diploma in Data Analytics"}
    assert find_courses("Fees for the Diploma in Data Analytics?", RULES, courses) == ["New.pdf"]


def test_rules_for_a_course_that_is_not_indexed_are_ignored() -> None:
    assert find_courses("How long is the Java course?", RULES, {PY: COURSES[PY]}) == []


@pytest.mark.parametrize("question, intent", [
    ("What is the duration of the course?", "duration_and_price"),
    ("Who teaches this?", "instructor_and_ratings"),
    ("Does it teach decorators?", "topic_coverage"),
    ("What are the prerequisites?", "requirements"),
    ("Who is this course for?", "audience"),
    ("What will I learn?", "learning_outcomes"),
    ("List all topics", "curriculum_list"),
    ("Hello there", "general"),
])
def test_question_types(question: str, intent: str) -> None:
    assert route_question(question, RULES, COURSES).intent == intent


def test_build_filter_avoids_single_item_and() -> None:
    assert build_filter(None, [], None) is None
    assert build_filter("A.pdf", [], None) == {"pdf_name": "A.pdf"}
    assert "$and" in build_filter("A.pdf", ["facts"], "Course overview")


# ---- search over a small fake index ---------------------------------------------------
def fake_embed(texts: List[str]) -> List[List[float]]:
    """Same-word texts get similar vectors: one dimension per hashed word, then normalised."""
    vectors = []
    for text in texts:
        v = [0.0] * 64
        for word in text.lower().split():
            v[int(hashlib.sha1(word.encode()).hexdigest(), 16) % 64] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        vectors.append([x / norm for x in v])
    return vectors


def _chunk(pdf: str, n: int, chunk_type: str, section: str, text: str) -> dict:
    return {"chunk_id": f"{pdf}::{chunk_type}::{n:03d}", "course_name": COURSES.get(pdf, pdf), "pdf_name": pdf, "page": 1,
            "page_end": 1, "section": section, "chunk_type": chunk_type, "text": text, "body": text,
            "source_sha1": "s", "content_hash": hashlib.sha1(text.encode()).hexdigest()[:16]}


@pytest.fixture
def retriever(tmp_path) -> Retriever:
    duration = "Duration, inclusions and price"
    chunks = [
        _chunk(PY, 1, "facts", duration, "python duration length price"),
        _chunk(PY, 2, "facts", "Course overview", "python instructor rating"),
        _chunk(PY, 1, "requirements", "Requirements", "python computer needed"),
        _chunk(PY, 1, "curriculum_overview", "Curriculum overview (part 1 of 2)", "python sections one"),
        _chunk(PY, 2, "curriculum_overview", "Curriculum overview (part 2 of 2)", "python sections two"),
        _chunk(PY, 1, "curriculum", "Decorators", "python decorators lecture"),
        _chunk(JAVA, 1, "facts", duration, "java duration length price"),
        _chunk("Other.pdf", 1, "section", "Fees", "brochure fees duration length"),
    ]
    client = open_client(tmp_path / "chroma")
    index_chunks({"documents": [], "chunks": chunks}, fake_embed, client, collection_name="test_chunks")
    return Retriever(get_collection(client, "test_chunks"), fake_embed, RULES, top_k=4, min_similarity=0.05)


def test_fact_question_returns_one_chunk_of_the_right_course(retriever: Retriever) -> None:
    result = retriever.retrieve("python course duration")
    assert [c.chunk_id for c in result.chunks] == [f"{PY}::facts::001"]


def test_comparison_returns_each_course(retriever: Retriever) -> None:
    result = retriever.retrieve("compare python and java price")
    assert {c.document for c in result.chunks} == {PY, JAVA}


def test_fact_question_with_no_course_covers_every_course(retriever: Retriever) -> None:
    result = retriever.retrieve("which duration length is longest")
    assert {PY, JAVA} <= {c.document for c in result.chunks}


def test_curriculum_list_returns_every_part_in_order(retriever: Retriever) -> None:
    result = retriever.retrieve("list all sections of python")
    assert [c.section[-11:] for c in result.chunks] == ["(part 1 of 2)"[-11:], "(part 2 of 2)"[-11:]]


def test_curriculum_list_with_no_course_asks_which_course(retriever: Retriever) -> None:
    result = retriever.retrieve("list all sections")
    assert result.needs_course and result.chunks == []


def test_other_layout_falls_back_to_the_whole_course(retriever: Retriever) -> None:
    rules = {**RULES, "courses": [{"pdf_name": "Other.pdf", "aliases": ["brochure"]}]}
    r = Retriever(retriever.collection, fake_embed, rules, top_k=4, min_similarity=0.05)
    result = r.retrieve("brochure duration length")
    assert [c.chunk_type for c in result.chunks] == ["section"]


def test_off_topic_question_is_rejected(retriever: Retriever) -> None:
    result = retriever.retrieve("zzz qqq xxx")
    assert not result.in_scope and result.chunks == []


def test_empty_question_is_an_error(retriever: Retriever) -> None:
    with pytest.raises(ValueError):
        retriever.retrieve("   ")
