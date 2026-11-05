"""Embeddings.

Purpose
-------
Turn text into vectors with all-MiniLM-L6-v2, running locally on the CPU. The model is
downloaded once and loaded once per process. Indexing and question answering must both use
this module so that chunks and questions are embedded the same way.

Vectors are normalised, so cosine similarity in ChromaDB compares direction only.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import List

from app import config

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_model():
    """Load the embedding model once. Imported here because the import itself takes seconds."""
    from sentence_transformers import SentenceTransformer

    try:
        model = SentenceTransformer(config.EMBEDDING_MODEL, device="cpu")
    except Exception as exc:
        raise RuntimeError(f"Could not load embedding model '{config.EMBEDDING_MODEL}'. The first run needs "
                           f"internet access to download it. Cause: {exc}") from exc
    log.info("Loaded %s (max_seq_length=%s)", config.EMBEDDING_MODEL, model.max_seq_length)
    return model


def embed_texts(texts: List[str]) -> List[List[float]]:
    vectors = get_model().encode(texts, batch_size=config.EMBEDDING_BATCH_SIZE, normalize_embeddings=True,
                                 convert_to_numpy=True, show_progress_bar=False)
    return vectors.tolist()


def token_counts(texts: List[str]) -> List[int]:
    """Real token count of each text, special tokens included, without the model's truncation.
    Anything above `max_seq_length` would lose its end when embedded."""
    tokenizer = get_model().tokenizer
    return [len(ids) for ids in tokenizer(texts, add_special_tokens=True, truncation=False, verbose=False)["input_ids"]]


def max_input_tokens() -> int:
    return get_model().max_seq_length
