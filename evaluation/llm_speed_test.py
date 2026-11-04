"""Measures how long the local LLM takes to answer a RAG-sized prompt on this machine.

    python -m evaluation.llm_speed_test
    python -m evaluation.llm_speed_test --runs 3 --context-tokens 800

The first run includes loading the model into memory. Each run sends different chunks, because
Ollama reuses its cache for a repeated prompt and that would hide the real cost of reading it.
Uses Ollama's HTTP API directly, so it needs no extra library.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request

from app import config

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:4b")
QUESTION = "What is the total length of this course, and what does it include?"
SYSTEM = "You are a Student Course Information Assistant. Answer using only the supplied course context. Be concise."


def build_context(target_tokens: int, run: int) -> str:
    """Real chunks up to about target_tokens, the size of what retrieval will send to the LLM.
    Each run starts at a different place in the file so no two prompts share their context."""
    parts, used = [], 0
    chunks = json.loads((config.PROCESSED_DIR / "chunks.json").read_text(encoding="utf-8"))["chunks"]
    for chunk in chunks[run * 37:]:
        if used >= target_tokens:
            break
        parts.append(chunk["text"])
        used += chunk["tokens_est"]
    return "\n\n".join(parts)


def ask(context: str, timeout: int) -> dict:
    body = {"model": OLLAMA_MODEL, "stream": False, "think": False,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {QUESTION}"}],
            "options": {"temperature": 0.1, "presence_penalty": 0, "num_ctx": 4096, "num_predict": 300}}
    request = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--context-tokens", type=int, default=800)
    ap.add_argument("--timeout", type=int, default=540, help="seconds to wait for one answer")
    args = ap.parse_args()

    print(f"Model: {OLLAMA_MODEL} | chunks file: {config.PROCESSED_DIR / 'chunks.json'}")
    for run in range(1, args.runs + 1):
        context = build_context(args.context_tokens, run)
        start = time.perf_counter()
        try:
            r = ask(context, args.timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SystemExit(f"Run {run} failed: {exc}. Is Ollama running, and is {OLLAMA_MODEL} installed?")
        total = time.perf_counter() - start
        seconds = lambda key: r.get(key, 0) / 1e9
        prompt_n, out_n = r.get("prompt_eval_count", 0), r.get("eval_count", 0)
        print(f"\nRun {run}: {total:.1f}s total")
        print(f"  load model     {seconds('load_duration'):.1f}s")
        print(f"  read prompt    {seconds('prompt_eval_duration'):.1f}s for {prompt_n} tokens")
        print(f"  write answer   {seconds('eval_duration'):.1f}s for {out_n} tokens "
              f"({out_n / max(seconds('eval_duration'), 0.001):.1f} tokens/s)")
        print(f"  answer: {r['message']['content'][:300]}")


if __name__ == "__main__":
    main()
