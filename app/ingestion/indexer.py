"""Indexing: data/processed/chunks.json -> persistent ChromaDB collection.

    python -m app.ingestion.indexer              # update: only what changed
    python -m app.ingestion.indexer --rebuild    # delete the collection and index everything

Update rules, per PDF
---------------------
* Same chunk ids, content hashes and PDF hash as the index: skipped, nothing is embedded.
* Anything different: the course's old chunks are deleted and the new ones added. Hashes are
  compared per chunk, not only per PDF, because editing ingestion_rules.json changes chunks
  while the PDF stays the same.
* Status FAIL in chunks.json: left alone, so a bad upload never erases a working course.
* In the index but not in chunks.json: reported and kept. Use --rebuild to remove it.

New vectors are computed before anything is deleted, so a failed embedding changes nothing.
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import chromadb
from chromadb.config import Settings

from app import config

log = logging.getLogger("indexer")

METADATA_KEYS = ("course_name", "pdf_name", "page", "page_end", "section", "chunk_type",
                 "captured_on", "source_sha1", "content_hash", "body")

Embedder = Callable[[List[str]], List[List[float]]]


@dataclass
class IndexResult:
    added: Dict[str, int] = field(default_factory=dict)      # pdf_name -> chunks added
    replaced: Dict[str, int] = field(default_factory=dict)   # pdf_name -> chunks after replacing
    unchanged: List[str] = field(default_factory=list)
    kept_failed: List[str] = field(default_factory=list)
    orphaned: List[str] = field(default_factory=list)
    total: int = 0


def load_chunks(path: Optional[Path] = None) -> dict:
    path = Path(path or config.PROCESSED_DIR / "chunks.json")
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run: python -m app.ingestion.pipeline")
    return json.loads(path.read_text(encoding="utf-8"))


def to_metadata(chunk: dict) -> dict:
    """ChromaDB accepts only str/int/float/bool values, so empty fields are left out."""
    return {k: chunk[k] for k in METADATA_KEYS if chunk.get(k) is not None}


def open_client(path: Optional[Path] = None):
    path = Path(path or config.CHROMA_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))


def get_collection(client, name: Optional[str] = None):
    """embedding_function=None: vectors always come from app.retrieval.embeddings, never from
    ChromaDB's default model. The model name is stored so a mismatch is caught, not ignored."""
    collection = client.get_or_create_collection(
        name=name or config.CHROMA_COLLECTION, embedding_function=None,
        metadata={"hnsw:space": "cosine", "embedding_model": config.EMBEDDING_MODEL})
    stored = (collection.metadata or {}).get("embedding_model")
    if stored != config.EMBEDDING_MODEL:
        raise ValueError(f"The index was built with '{stored}' but the configured model is "
                         f"'{config.EMBEDDING_MODEL}'. Run the indexer with --rebuild.")
    return collection


def _indexed_by_pdf(collection) -> Dict[str, Dict[str, Tuple[str, str]]]:
    """pdf_name -> {chunk_id: (content_hash, source_sha1)} for everything in the collection."""
    found: Dict[str, Dict[str, Tuple[str, str]]] = {}
    stored = collection.get(include=["metadatas"])
    for chunk_id, meta in zip(stored["ids"], stored["metadatas"]):
        found.setdefault(meta["pdf_name"], {})[chunk_id] = (meta.get("content_hash"), meta.get("source_sha1"))
    return found


def _add(collection, chunks: List[dict], vectors: List[List[float]]) -> None:
    collection.upsert(ids=[c["chunk_id"] for c in chunks], embeddings=vectors,
                      documents=[c["text"] for c in chunks], metadatas=[to_metadata(c) for c in chunks])


def index_chunks(payload: dict, embed: Embedder, client, collection_name: Optional[str] = None,
                 rebuild: bool = False) -> IndexResult:
    name = collection_name or config.CHROMA_COLLECTION
    failed = [d["pdf_name"] for d in payload.get("documents", []) if d.get("status") == "FAIL"]
    if rebuild and failed:
        raise ValueError(f"Refusing to rebuild: {failed} failed ingestion, so rebuilding would erase those "
                         "courses. Fix them first, or run without --rebuild to keep their old chunks.")

    by_pdf: Dict[str, List[dict]] = {}
    for chunk in payload["chunks"]:
        by_pdf.setdefault(chunk["pdf_name"], []).append(chunk)

    if rebuild and name in [getattr(c, "name", c) for c in client.list_collections()]:
        new_vectors = {pdf: embed([c["text"] for c in chunks]) for pdf, chunks in by_pdf.items()}
        client.delete_collection(name)
    else:
        new_vectors = None

    collection = get_collection(client, name)
    indexed = _indexed_by_pdf(collection)
    result = IndexResult(kept_failed=[p for p in failed if p in indexed])

    for pdf, chunks in by_pdf.items():
        if pdf in failed:
            continue
        wanted = {c["chunk_id"]: (c["content_hash"], c["source_sha1"]) for c in chunks}
        current = indexed.get(pdf)
        if current == wanted:
            result.unchanged.append(pdf)
            continue
        vectors = new_vectors[pdf] if new_vectors else embed([c["text"] for c in chunks])
        if current:
            collection.delete(ids=list(current))
        _add(collection, chunks, vectors)
        (result.replaced if current else result.added)[pdf] = len(chunks)
        log.info("%s: %s %d chunks", pdf, "replaced with" if current else "added", len(chunks))

    result.orphaned = sorted(set(indexed) - set(by_pdf) - set(failed))
    result.total = collection.count()
    return result


def check_token_lengths(chunks: List[dict], counter: Callable[[List[str]], List[int]], limit: int) -> dict:
    counts = counter([c["text"] for c in chunks])
    over = [(c["chunk_id"], n) for c, n in zip(chunks, counts) if n > limit]
    return {"limit": limit, "max": max(counts), "avg": sum(counts) // len(counts), "over_limit": over}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true", help="delete the collection and index everything again")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from app.retrieval.embeddings import embed_texts, max_input_tokens, token_counts

    try:
        payload = load_chunks()
        limit = max_input_tokens()
        print(f"Embedding model: {config.EMBEDDING_MODEL} (max_seq_length={limit})")
        tokens = check_token_lengths(payload["chunks"], token_counts, limit)
        print(f"Real tokens per chunk: avg {tokens['avg']}, max {tokens['max']}, limit {limit}")
        for chunk_id, n in tokens["over_limit"]:
            print(f"  WARN over the limit, its end would not be embedded: {chunk_id} ({n} tokens)")
        result = index_chunks(payload, embed_texts, open_client(), rebuild=args.rebuild)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise SystemExit(f"Indexing failed: {exc}")

    print(f"Added: {result.added or 'none'}")
    print(f"Replaced: {result.replaced or 'none'}")
    print(f"Unchanged: {result.unchanged or 'none'}")
    if result.kept_failed:
        print(f"WARN kept old chunks of failed PDFs: {result.kept_failed}")
    if result.orphaned:
        print(f"WARN in the index but not in chunks.json (use --rebuild to remove): {result.orphaned}")
    print(f"Collection '{config.CHROMA_COLLECTION}' now has {result.total} chunks.")


if __name__ == "__main__":
    main()
