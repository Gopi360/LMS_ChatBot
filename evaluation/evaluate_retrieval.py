"""Scores retrieval against evaluation/retrieval_questions.json. No LLM is used.

    python -m evaluation.evaluate_retrieval                # dev set, used while tuning
    python -m evaluation.evaluate_retrieval --set held_out # final score, not for tuning
    python -m evaluation.evaluate_retrieval --baseline     # plain top-k with no routing
    python -m evaluation.evaluate_retrieval --scores       # similarity spread, to set the cutoff
    python -m evaluation.evaluate_retrieval --patterns-only # routing without the example questions

A question passes when every expected course has a chunk of the expected type (and section,
when given) in the results. An out-of-scope question passes when it is rejected. "all_parts"
requires every part of a course's curriculum overview to be returned.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import List, Tuple

from app.retrieval.retriever import RetrievalResult, RetrievedChunk, Route, get_retriever

QUESTIONS_PATH = Path(__file__).with_name("retrieval_questions.json")


def passes(item: dict, result: RetrievalResult) -> Tuple[bool, str]:
    if item.get("scope") is False:
        return (not result.in_scope, "" if not result.in_scope else "should have been rejected")
    if not result.in_scope:
        return False, f"rejected as out of scope ({result.best_similarity:.3f})"
    for course in item["pdf"]:
        hits = [c for c in result.chunks if c.document.lower().find(course.lower()) >= 0
                and c.chunk_type in item["types"] and item.get("section", "").lower() in c.section.lower()]
        if not hits:
            return False, f"no {item['types']} chunk for {course}"
        if item.get("all_parts") and len(hits) < item["all_parts"]:
            return False, f"{len(hits)} of {item['all_parts']} parts for {course}"
    return True, ""


def plain_search(retriever, question: str, top_k: int) -> RetrievalResult:
    """The baseline: top k by similarity alone, as it worked before routing."""
    chunks: List[RetrievedChunk] = retriever._search(retriever.embed([question])[0], None, top_k)
    return RetrievalResult(question=question, route=Route(), best_similarity=chunks[0].similarity, chunks=chunks)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", choices=["dev", "held_out"], default="dev")
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--scores", action="store_true")
    ap.add_argument("--patterns-only", action="store_true")
    args = ap.parse_args()

    items = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))[args.set]
    retriever = get_retriever()
    if args.patterns_only:
        retriever.matcher = None
    in_scope = [i for i in items if i.get("scope") is not False]
    results = []
    for item in items:
        result = plain_search(retriever, item["q"], retriever.top_k) if args.baseline else retriever.retrieve(item["q"])
        results.append((item, result, *passes(item, result)))

    if args.scores:
        best = lambda rows: sorted(round(r.best_similarity, 3) for _, r, _, _ in rows)
        print("in scope    :", best([x for x in results if x[0].get("scope") is not False]))
        print("out of scope:", best([x for x in results if x[0].get("scope") is False]))
        return

    failed = [(i, r, why) for i, r, ok, why in results if not ok]
    good_scope = sum(ok for i, _, ok, _ in results if i.get("scope") is not False)
    good_out = sum(ok for i, _, ok, _ in results if i.get("scope") is False)
    sizes = [len(r.chunks) for i, r, _, _ in results if i.get("scope") is not False]
    tokens = [sum(len(c.text) // 4 for c in r.chunks) for i, r, _, _ in results if i.get("scope") is not False]
    label = "baseline (no routing)" if args.baseline else "routed"
    print(f"Set: {args.set} | {label}")
    print(f"Course questions: {good_scope}/{len(in_scope)} passed")
    if not args.baseline:
        print(f"Off-topic questions rejected: {good_out}/{len(items) - len(in_scope)}")
    print(f"Chunks per question: average {mean(sizes):.1f}, max {max(sizes)} | "
          f"approx. context tokens: average {mean(tokens):.0f}, max {max(tokens)}")
    for item, result, why in failed:
        if args.baseline and item.get("scope") is False:
            continue
        print(f"  FAIL {item['q']}\n       {why} | routed to {result.route.courses or '-'} / {result.route.intent}")


if __name__ == "__main__":
    main()
