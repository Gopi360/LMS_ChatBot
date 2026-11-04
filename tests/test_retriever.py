"""Routing and search behaviour, with a tiny fake index so no model download is needed."""
from __future__ import annotations

import hashlib
from typing import List

import pytest

from app.ingestion.indexer import get_collection, index_chunks, open_client
from app.retrieval.retriever import (IntentMatcher, Retriever, build_filter, find_courses, load_rules, mask_courses,
                                     route_question)

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
    ("How much RAM do I need?", "requirements"),
    ("How much does it cost?", "duration_and_price"),
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
    return Retriever(get_collection(client, "test_chunks"), fake_embed, RULES, top_k=4, min_similarity=0.05,
                     min_similarity_no_course=0.05)


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


def test_a_list_request_with_no_course_asks_which_course_even_with_a_weak_match(retriever: Retriever) -> None:
    strict = Retriever(retriever.collection, fake_embed, RULES, top_k=4, min_similarity=0.05, min_similarity_no_course=0.99)
    result = strict.retrieve("list all sections")
    assert result.in_scope and result.needs_course


def test_other_layout_falls_back_to_the_whole_course(retriever: Retriever) -> None:
    rules = {**RULES, "courses": [{"pdf_name": "Other.pdf", "aliases": ["brochure"]}]}
    r = Retriever(retriever.collection, fake_embed, rules, top_k=4, min_similarity=0.05, min_similarity_no_course=0.05)
    result = r.retrieve("brochure duration length")
    assert [c.chunk_type for c in result.chunks] == ["section"]


def test_off_topic_question_is_rejected(retriever: Retriever) -> None:
    result = retriever.retrieve("zzz qqq xxx")
    assert not result.in_scope and result.chunks == []


def test_no_course_named_needs_a_stronger_match(retriever: Retriever) -> None:
    strict = Retriever(retriever.collection, fake_embed, RULES, top_k=4, min_similarity=0.05, min_similarity_no_course=0.99)
    assert not strict.retrieve("duration length price").in_scope
    assert strict.retrieve("python duration length price").in_scope


def test_empty_question_is_an_error(retriever: Retriever) -> None:
    with pytest.raises(ValueError):
        retriever.retrieve("   ")


# ---- matching a question to example questions -----------------------------------------
EXAMPLE_RULES = {"intents": [
    {"name": "instructor_and_ratings", "chunk_types": ["facts"], "section": "Course overview", "max_chunks": 1,
     "patterns": [r"\binstructor\b"], "examples": ["alpha beta gamma delta"]},
    {"name": "requirements", "chunk_types": ["requirements"], "max_chunks": 2,
     "patterns": [], "examples": ["alpha beta gamma epsilon", "zeta eta theta iota"]}],
    "no_intent_examples": ["kappa lambda mu nu"]}


def _matcher(min_similarity: float = 0.5, min_margin: float = 0.0) -> IntentMatcher:
    return IntentMatcher(EXAMPLE_RULES, fake_embed, min_similarity, min_margin)


def test_matcher_picks_the_intent_of_the_closest_example() -> None:
    assert _matcher().match("zeta eta theta iota") == "requirements"


def test_matcher_gives_up_below_the_similarity_cutoff() -> None:
    assert _matcher(min_similarity=0.99).match("zeta eta theta") is None


def test_matcher_gives_up_when_two_intents_are_too_close() -> None:
    assert _matcher(min_margin=0.3).match("alpha beta gamma") is None


def test_matcher_stays_general_on_a_no_intent_example() -> None:
    assert _matcher().match("kappa lambda mu nu") is None


def test_matcher_embeds_the_examples_only_once() -> None:
    calls = []

    def counting(texts: List[str]) -> List[List[float]]:
        calls.append(texts)
        return fake_embed(texts)

    matcher = IntentMatcher(EXAMPLE_RULES, counting, 0.5, 0.0)
    assert not calls                                  # nothing is embedded until it is needed
    matcher.match("zeta eta")
    matcher.match("zeta eta")
    assert len([c for c in calls if len(c) > 1]) == 1


def test_matcher_with_no_examples_never_matches() -> None:
    assert IntentMatcher({"intents": []}, fake_embed, 0.0, 0.0).match("anything") is None


@pytest.mark.parametrize("question, courses, masked", [
    ("Who runs the AWS course?", [AWS], "Who runs this course?"),
    ("Does the AWS course use AWS Lambda?", [AWS], "Does this course use AWS Lambda?"),
    ("Who runs the Python programming course?", [PY], "Who runs this course?"),
    ("aws course whats the full breakdown", [AWS], "this course whats the full breakdown"),
    ("What are the goals of the machine learning course?", [ML], "What are the goals of this course?"),
    ("Does the Machine Learning course use Python?", [ML], "Does this course use Python?"),
    ("Is the python bootcamp hard?", [PY], "Is this course hard?"),
])
def test_mask_courses_replaces_only_the_routed_course(question: str, courses: List[str], masked: str) -> None:
    assert mask_courses(question, courses, RULES, COURSES) == masked


@pytest.mark.parametrize("question, course", [
    ("What are the parts of a Java class?", JAVA),
    ("How do I write a Python class?", PY),
    ("What is cloud storage?", AWS),
    ("Is Python free?", PY),
    ("who runs aws", AWS),
])
def test_a_bare_name_may_be_the_topic_so_it_is_not_masked(question: str, course: str) -> None:
    assert mask_courses(question, [course], RULES, COURSES) is None


def test_two_questions_in_one_count_as_a_comparison() -> None:
    found = find_courses("Who teaches Python and who teaches Java?", RULES, COURSES)
    assert found == [PY, JAVA]


@pytest.mark.parametrize("question, course", [
    ("Does the Machine Learning course use Python and how long is it?", ML),
    ("Does the AWS course cover machine learning and what are the prerequisites?", AWS),
])
def test_a_second_question_about_the_same_course_is_not_a_comparison(question: str, course: str) -> None:
    assert find_courses(question, RULES, COURSES) == [course]


def _with_examples(retriever: Retriever, examples: List[str], **kw) -> Retriever:
    rules = {**RULES, "intents": [{"name": "requirements", "chunk_types": ["requirements"], "max_chunks": 2,
                                   "patterns": [r"\bprerequisites\b"], "examples": examples}]}
    return Retriever(retriever.collection, fake_embed, rules, top_k=4, min_similarity=0.05, min_similarity_no_course=0.05,
                     min_intent_similarity=0.5, min_intent_margin=0.0, **kw)


def test_example_match_narrows_the_search_when_no_pattern_fits(retriever: Retriever) -> None:
    result = _with_examples(retriever, ["what must i bring to this course"]).retrieve("What must I bring to the python course?")
    assert result.route.intent == "requirements" and result.route.matched_by == "example"
    assert [c.chunk_type for c in result.chunks] == ["requirements"]


def test_a_pattern_match_is_never_overridden_by_examples(retriever: Retriever) -> None:
    result = _with_examples(retriever, ["nothing like it"]).retrieve("python prerequisites")
    assert result.route.intent == "requirements" and result.route.matched_by == "pattern"


def test_examples_are_skipped_when_no_course_is_named(retriever: Retriever) -> None:
    result = _with_examples(retriever, ["what must i bring"]).retrieve("what must i bring")
    assert result.route.intent == "general"


def test_examples_can_be_switched_off(retriever: Retriever) -> None:
    r = _with_examples(retriever, ["what must i bring to this course"], use_examples=False)
    assert r.retrieve("What must I bring to the python course?").route.intent == "general"


def _one_intent(retriever: Retriever, **intent) -> Retriever:
    rules = {**RULES, "intents": [{"name": "custom", "patterns": [], **intent}]}
    return Retriever(retriever.collection, fake_embed, rules, top_k=4, min_similarity=0.05, min_similarity_no_course=0.05,
                     min_intent_similarity=0.5, min_intent_margin=0.0)


def test_an_outline_found_by_example_keeps_a_normal_search_too(retriever: Retriever) -> None:
    r = _one_intent(retriever, chunk_types=["curriculum_overview"], fetch_all=True,
                    examples=["break down decorators for me in this course"])
    chunks = r.retrieve("break down decorators for me in the python course").chunks
    ids = [c.chunk_id for c in chunks]
    assert {"curriculum_overview", "curriculum"} <= {c.chunk_type for c in chunks} and len(ids) == len(set(ids))


def test_an_outline_found_by_pattern_stays_the_outline_only(retriever: Retriever) -> None:
    chunks = retriever.retrieve("list all sections of python").chunks
    assert {c.chunk_type for c in chunks} == {"curriculum_overview"}


def test_one_fact_limit_applies_after_an_example_match(retriever: Retriever) -> None:
    r = _one_intent(retriever, chunk_types=["facts"], max_chunks=1, examples=["what will this course set me back"])
    assert len(r.retrieve("what will the python course set me back").chunks) == 1


def test_an_intent_may_have_examples_and_no_patterns(retriever: Retriever) -> None:
    rules = {"intents": [{"name": "custom", "chunk_types": ["facts"], "examples": ["x"]}]}
    assert route_question("anything", rules, COURSES).intent == "general"


def test_an_intent_with_missing_fields_is_rejected_when_the_rules_load(tmp_path) -> None:
    path = tmp_path / "rules.json"
    path.write_text('{"intents": [{"name": "custom", "examples": ["x"]}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="custom"):
        load_rules(path)


# ---- follow-up questions --------------------------------------------------------------
def _loose(retriever: Retriever, follow_up_cutoff: float = 0.05) -> Retriever:
    return Retriever(retriever.collection, fake_embed, RULES, top_k=4, min_similarity=0.05, min_similarity_no_course=0.05,
                     min_similarity_follow_up=follow_up_cutoff)


def test_a_question_with_no_course_uses_the_remembered_one(retriever: Retriever) -> None:
    result = _loose(retriever).retrieve("what is the duration length price", default_course=PY)
    assert result.route.courses == [PY] and {c.document for c in result.chunks} == {PY}


def test_a_remembered_course_never_rescues_an_off_topic_question(retriever: Retriever) -> None:
    result = _loose(retriever, follow_up_cutoff=0.99).retrieve("is it going to rain today", default_course=PY)
    assert not result.in_scope and result.chunks == []


def test_the_course_title_is_not_read_as_part_of_the_question(retriever: Retriever) -> None:
    """The Java title contains "Hours", which must not make this a duration question."""
    assert _loose(retriever).retrieve("who is it for", default_course=JAVA).route.intent == "audience"


@pytest.mark.parametrize("question", ["which courses have a duration", "is there a course that covers decorators",
                                      "do you have any course on price"])
def test_a_question_about_all_courses_ignores_the_remembered_one(retriever: Retriever, question: str) -> None:
    assert _loose(retriever).retrieve(question, default_course=PY).route.courses == []


def test_a_course_name_after_it_is_the_topic(retriever: Retriever) -> None:
    assert _loose(retriever).retrieve("does it use java", default_course=PY).route.courses == [PY]


@pytest.mark.parametrize("question", ["what about the java course", "what about java", "does it compare to the java course"])
def test_naming_another_course_moves_on(retriever: Retriever, question: str) -> None:
    assert _loose(retriever).retrieve(question, default_course=PY).route.courses == [JAVA]


def test_a_follow_up_can_match_the_example_questions(retriever: Retriever) -> None:
    rules = {**RULES, "intents": [{"name": "requirements", "chunk_types": ["requirements"], "max_chunks": 2, "patterns": [],
                                   "examples": ["what must i bring for it"]}]}
    r = Retriever(retriever.collection, fake_embed, rules, top_k=4, min_similarity=0.05, min_similarity_no_course=0.05,
                  min_similarity_follow_up=0.05, min_intent_similarity=0.5, min_intent_margin=0.0)
    assert r.retrieve("what must i bring for it", default_course=PY).route.intent == "requirements"


def test_a_short_question_with_it_counts_as_being_about_the_course(retriever: Retriever) -> None:
    assert _loose(retriever, follow_up_cutoff=0.99).retrieve("who made it", default_course=PY).in_scope


def test_a_long_question_with_it_needs_a_real_match(retriever: Retriever) -> None:
    r = Retriever(retriever.collection, fake_embed, RULES, top_k=4, min_similarity=0.05, min_similarity_no_course=0.05,
                  min_similarity_follow_up=0.99, use_examples=False)
    assert not r.retrieve("what is the capital of france and is it big", default_course=PY).in_scope


def test_a_short_question_without_a_pronoun_needs_a_real_match(retriever: Retriever) -> None:
    assert not _loose(retriever, follow_up_cutoff=0.99).retrieve("tell me a joke", default_course=PY).in_scope


def test_a_follow_up_matching_an_example_is_in_scope(retriever: Retriever) -> None:
    question = "what do i have to bring along for the whole thing"
    rules = {**RULES, "intents": [{"name": "requirements", "chunk_types": ["requirements"], "patterns": [], "examples": [question]}]}
    r = Retriever(retriever.collection, fake_embed, rules, top_k=4, min_similarity=0.05, min_similarity_no_course=0.05,
                  min_similarity_follow_up=0.99)
    assert r.retrieve(question, default_course=PY).in_scope
