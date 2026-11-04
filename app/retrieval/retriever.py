"""Retrieval: find the chunks that can answer a student's question. No LLM is involved.

    python -m app.retrieval.retriever "How long is the Python course?"
    python -m app.retrieval.retriever            # interactive

Plain similarity search is not enough here. Every chunk starts with its course name, so a
question naming a course matches that whole course and the right *kind* of chunk gets lost
among the rest. So each question is routed first, using config/retrieval_rules.json:

1. Which course?   Short names ("AWS", "ML"). Two courses only when the question compares them.
2. What kind?      Duration -> facts, prerequisites -> requirements, "does it cover X" -> curriculum.
                   Patterns decide first, because they are precise. If none fits and a course is
                   named, the question is compared by meaning with example questions, which catches
                   wordings no pattern lists ("Who runs the AWS course?"). A match must be strong
                   and clearly ahead of the next type; otherwise the search stays broad, because a
                   wrong narrow search loses the answer while a broad one usually still finds it.
3. Search          Inside that course and chunk type. Facts questions need one chunk, not four,
                   and a smaller context means a faster answer from the LLM later.

Questions matching nothing in the documents are marked out of scope by a similarity cutoff.
The cutoff is stricter when no course is named, because a phrase such as "the price of"
resembles course text even in "What is the price of bitcoin?".
If a chunk type does not exist for a course (a PDF with another layout), the search widens to
the whole course, so new layouts degrade gracefully instead of returning nothing.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from app import config

log = logging.getLogger(__name__)

Embedder = Callable[[List[str]], List[List[float]]]


@dataclass
class Route:
    courses: List[str] = field(default_factory=list)     # pdf names; empty = no course named
    intent: str = "general"
    chunk_types: List[str] = field(default_factory=list)
    section: Optional[str] = None
    max_chunks: Optional[int] = None
    fetch_all: bool = False
    matched_by: Optional[str] = None                     # "pattern", "example", or None for general


@dataclass
class RetrievedChunk:
    chunk_id: str
    document: str
    course_name: str
    page: int
    page_end: int
    section: str
    chunk_type: str
    similarity: Optional[float]      # None for chunks fetched whole, not ranked by a search
    text: str


@dataclass
class RetrievalResult:
    question: str
    route: Route
    in_scope: bool = True
    needs_course: bool = False       # the question needs a course name; ask the student which one
    best_similarity: float = 0.0
    chunks: List[RetrievedChunk] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def load_rules(path: Optional[Path] = None) -> dict:
    rules = json.loads(Path(path or config.RETRIEVAL_RULES_PATH).read_text(encoding="utf-8"))
    for intent in rules.get("intents", []):
        if "name" not in intent or "chunk_types" not in intent:
            raise ValueError(f"Question type {intent.get('name', intent)} needs a name and chunk_types.")
    return rules


# ---- routing --------------------------------------------------------------------------
def _alias_table(rules: dict, course_names: Dict[str, str]) -> List[Tuple[str, str]]:
    """(alias, pdf_name), longest first so "machine learning a-z" wins over "machine learning".
    The full title of every indexed course always counts, even with no entry in the rules."""
    table = [(name.lower(), pdf) for pdf, name in course_names.items()]
    for course in rules.get("courses", []):
        if course["pdf_name"] in course_names:
            table += [(alias.lower(), course["pdf_name"]) for alias in course["aliases"]]
    return sorted(set(table), key=lambda item: -len(item[0]))


def _course_mentions(question: str, rules: dict, course_names: Dict[str, str]) -> List[Tuple[int, int, str, bool]]:
    """(start, end, pdf, followed by "course") for each course named, in reading order.
    `end` includes the word "course" when it follows."""
    taken = [False] * len(question)
    found: List[Tuple[int, int, str, bool]] = []
    after = re.compile(rules.get("course_word_after_alias", r"$^"), re.IGNORECASE)
    for alias, pdf in _alias_table(rules, course_names):
        for m in re.finditer(rf"(?<![\w-]){re.escape(alias)}(?![\w-])", question, re.IGNORECASE):
            if any(taken[m.start():m.end()]):
                continue
            taken[m.start():m.end()] = [True] * (m.end() - m.start())
            word = after.match(question, m.end())
            found.append((m.start(), word.end() if word else m.end(), pdf, bool(word)))
    return sorted(found)


def find_courses(question: str, rules: dict, course_names: Dict[str, str]) -> List[str]:
    """Courses named in the question. Two only when it compares them, because a course title
    or topic often names another course: "Does the Machine Learning course use Python?"."""
    found = _course_mentions(question, rules, course_names)
    ordered: List[str] = []
    for _, _, pdf, _ in found:
        if pdf not in ordered:
            ordered.append(pdf)
    if len(ordered) < 2:
        return ordered
    if any(re.search(p, question.lower()) for p in rules.get("compare_patterns", [])):
        return ordered
    second = re.search(rules.get("second_question_pattern", r"$^"), question, re.IGNORECASE)
    if second and found[0][0] < second.start() < found[-1][0]:      # a course on each side of it
        return ordered
    named_as_course = [pdf for _, _, pdf, followed in found if followed]
    return [named_as_course[0] if named_as_course else ordered[0]]


def mask_courses(question: str, courses: List[str], rules: dict, course_names: Dict[str, str]) -> Optional[str]:
    """Replaces the routed course with "this course", so that a match with the example questions
    depends on what is being asked, not on which course it is about. Only one mention per course
    changes, and other names stay: in "Does the AWS course use AWS Lambda?" the second AWS is the
    topic. Returns None if the name could just as well be the topic ("Is Python free?",
    "a Java class"), which leaves the search broad."""
    clear_word = re.compile(rules.get("clear_course_word", r"$^"), re.IGNORECASE)
    chosen: Dict[str, Tuple[int, int, bool]] = {}
    for start, end, pdf, _ in _course_mentions(question, rules, course_names):
        lead = re.search(r"\b(the|this|that|my)\s+$", question[:start], re.IGNORECASE)
        clear = bool(clear_word.search(question[start:end]))
        if pdf in courses and (lead or clear) and (pdf not in chosen or (clear and not chosen[pdf][2])):
            chosen[pdf] = (lead.start() if lead else start, end, clear)
    if not chosen:
        return None
    for start, end, _ in sorted(chosen.values(), reverse=True):
        question = question[:start] + "this course" + question[end:]
    return question


def _apply_intent(route: Route, intent: dict, matched_by: str) -> None:
    route.intent = intent["name"]
    route.chunk_types = intent["chunk_types"]
    route.section = intent.get("section")
    route.max_chunks = intent.get("max_chunks")
    route.fetch_all = intent.get("fetch_all", False)
    route.matched_by = matched_by


def route_question(question: str, rules: dict, course_names: Dict[str, str]) -> Route:
    route = Route(courses=find_courses(question, rules, course_names))
    for intent in rules.get("intents", []):
        if any(re.search(p, question, re.IGNORECASE) for p in intent.get("patterns", [])):
            _apply_intent(route, intent, "pattern")
            break
    return route


class IntentMatcher:
    """Finds a question's type by comparing it with the example questions in the rules.
    "no_intent_examples" are broad questions: matching one of them keeps the search broad."""

    def __init__(self, rules: dict, embed: Embedder, min_similarity: float, min_margin: float) -> None:
        self.embed = embed
        self.min_similarity = min_similarity
        self.min_margin = min_margin
        self.examples: List[Tuple[Optional[str], str]] = [
            (intent["name"], text) for intent in rules.get("intents", []) for text in intent.get("examples", [])]
        self.examples += [(None, text) for text in rules.get("no_intent_examples", [])]
        self._vectors: Optional[List[List[float]]] = None        # embedded on first use

    def match(self, question: str) -> Optional[str]:
        if not self.examples:
            return None
        if self._vectors is None:
            self._vectors = self.embed([text for _, text in self.examples])
        vector = self.embed([question])[0]
        best: Dict[Optional[str], float] = {}                    # vectors are normalised: dot = cosine
        for (intent, _), example in zip(self.examples, self._vectors):
            score = sum(a * b for a, b in zip(vector, example))
            best[intent] = max(score, best.get(intent, -1.0))
        ranked = sorted(best.items(), key=lambda item: -item[1])
        winner, score = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else -1.0
        if winner is None or score < self.min_similarity or score - runner_up < self.min_margin:
            return None
        return winner


# ---- search ---------------------------------------------------------------------------
def build_filter(pdf: Optional[str], chunk_types: List[str], section: Optional[str]) -> Optional[dict]:
    """A ChromaDB `where` filter. It rejects a single-item $and, so that case is unwrapped."""
    parts: List[dict] = []
    if pdf:
        parts.append({"pdf_name": pdf})
    if chunk_types:
        parts.append({"chunk_type": {"$in": chunk_types}})
    if section:
        parts.append({"section": section})
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else {"$and": parts}


def _to_chunk(chunk_id: str, text: str, meta: dict, similarity: Optional[float]) -> RetrievedChunk:
    return RetrievedChunk(chunk_id=chunk_id, document=meta["pdf_name"], course_name=meta["course_name"],
                          page=meta["page"], page_end=meta["page_end"], section=meta["section"],
                          chunk_type=meta["chunk_type"], similarity=similarity, text=text)


class Retriever:
    def __init__(self, collection, embed: Embedder, rules: Optional[dict] = None,
                 top_k: Optional[int] = None, min_similarity: Optional[float] = None,
                 min_similarity_no_course: Optional[float] = None, min_intent_similarity: Optional[float] = None,
                 min_intent_margin: Optional[float] = None, use_examples: bool = True) -> None:
        self.collection = collection
        self.embed = embed
        self.rules = rules if rules is not None else load_rules()
        self.top_k = top_k or config.RETRIEVAL_TOP_K
        self.min_similarity = config.RETRIEVAL_MIN_SIMILARITY if min_similarity is None else min_similarity
        self.min_similarity_no_course = (config.RETRIEVAL_MIN_SIMILARITY_NO_COURSE if min_similarity_no_course is None
                                         else min_similarity_no_course)
        self.matcher: Optional[IntentMatcher] = None
        if use_examples:
            self.matcher = IntentMatcher(
                self.rules, embed,
                config.RETRIEVAL_MIN_INTENT_SIMILARITY if min_intent_similarity is None else min_intent_similarity,
                config.RETRIEVAL_MIN_INTENT_MARGIN if min_intent_margin is None else min_intent_margin)
        stored = collection.get(include=["metadatas"])["metadatas"]
        self.course_names = {m["pdf_name"]: m["course_name"] for m in stored}

    def _search(self, vector: List[float], where: Optional[dict], n: int) -> List[RetrievedChunk]:
        found = self.collection.query(query_embeddings=[vector], n_results=n, where=where,
                                      include=["documents", "metadatas", "distances"])
        return [_to_chunk(i, d, m, round(1 - dist, 4)) for i, d, m, dist in
                zip(found["ids"][0], found["documents"][0], found["metadatas"][0], found["distances"][0])]

    def _fetch_all(self, pdf: str, chunk_types: List[str]) -> List[RetrievedChunk]:
        found = self.collection.get(where=build_filter(pdf, chunk_types, None), include=["documents", "metadatas"])
        chunks = [_to_chunk(i, d, m, None) for i, d, m in zip(found["ids"], found["documents"], found["metadatas"])]
        return sorted(chunks, key=lambda c: c.chunk_id)

    def _search_course(self, vector: List[float], pdf: Optional[str], route: Route, n: int) -> List[RetrievedChunk]:
        chunks = self._search(vector, build_filter(pdf, route.chunk_types, route.section), n)
        if not chunks and (route.chunk_types or route.section):
            log.info("No %s chunks for %s; searching the whole course", route.intent, pdf)
            chunks = self._search(vector, build_filter(pdf, [], None), n)
        return chunks

    def _match_examples(self, question: str, route: Route) -> None:
        """Only for a question no pattern recognised that names a course."""
        if not self.matcher or route.matched_by or not route.courses:
            return
        masked = mask_courses(question, route.courses, self.rules, self.course_names)
        name = self.matcher.match(masked) if masked else None
        if name:
            _apply_intent(route, next(i for i in self.rules["intents"] if i["name"] == name), "example")

    def retrieve(self, question: str, top_k: Optional[int] = None) -> RetrievalResult:
        question = question.strip()
        if not question:
            raise ValueError("The question is empty.")
        top_k = top_k or self.top_k
        route = route_question(question, self.rules, self.course_names)
        result = RetrievalResult(question=question, route=route)

        vector = self.embed([question])[0]
        best = self._search(vector, None, 1)
        result.best_similarity = best[0].similarity if best else 0.0
        cutoff = self.min_similarity if route.courses else self.min_similarity_no_course
        if result.best_similarity < cutoff:
            result.in_scope = False
            return result

        self._match_examples(question, route)
        if route.fetch_all:
            if not route.courses:
                result.needs_course = True
                return result
            result.chunks = [c for pdf in route.courses for c in self._fetch_all(pdf, route.chunk_types)]
            if result.chunks:
                if route.matched_by == "example":   # it may only sound like an outline request
                    per_course = math.ceil(top_k / len(route.courses))
                    seen = {c.chunk_id for c in result.chunks}
                    found = [c for pdf in route.courses for c in self._search(vector, build_filter(pdf, [], None), per_course)]
                    result.chunks += [c for c in found if c.chunk_id not in seen]
                return result

        if route.max_chunks:                    # one fact per course: every course if none is named
            targets: List[Optional[str]] = list(route.courses or sorted(self.course_names))
            per_target = route.max_chunks
        else:
            targets = list(route.courses) or [None]
            per_target = math.ceil(top_k / len(targets))
        result.chunks = [c for pdf in targets for c in self._search_course(vector, pdf, route, per_target)]
        return result


@lru_cache(maxsize=1)
def get_retriever() -> Retriever:
    """The retriever over the real index, built once per process."""
    from app.ingestion.indexer import get_collection, open_client
    from app.retrieval.embeddings import embed_texts

    collection = get_collection(open_client())
    if collection.count() == 0:
        raise RuntimeError("The index is empty. Run: python -m app.ingestion.indexer")
    return Retriever(collection, embed_texts)


def _show(result: RetrievalResult) -> None:
    r = result.route
    print(f"\nCourse: {r.courses or 'not named'} | type: {r.intent}{' (by example)' if r.matched_by == 'example' else ''} | best similarity: {result.best_similarity:.3f}")
    if not result.in_scope:
        print("Out of scope: nothing in the course documents matches this question.")
    if result.needs_course:
        print("Needs a course: ask the student which course they mean.")
    for c in result.chunks:
        score = "whole" if c.similarity is None else f"{c.similarity:.3f}"
        print(f"  [{score}] {c.document} p{c.page} | {c.chunk_type} | {c.section[:60]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", nargs="?", help="leave out for interactive mode")
    ap.add_argument("--top-k", type=int)
    ap.add_argument("--text", action="store_true", help="also print each chunk's text")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        retriever = get_retriever()
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(f"Retrieval is not ready: {exc}")

    def answer(question: str) -> None:
        result = retriever.retrieve(question, args.top_k)
        _show(result)
        if args.text:
            for chunk in result.chunks:
                print(f"\n--- {chunk.chunk_id} ---\n{chunk.text}")

    if args.question:
        answer(args.question)
        return
    print("Type a question, or press Enter on an empty line to quit.")
    while True:
        question = input("\n> ").strip()
        if not question:
            break
        answer(question)


if __name__ == "__main__":
    main()
