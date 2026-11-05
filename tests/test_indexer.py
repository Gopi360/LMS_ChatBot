"""Indexer behaviour, run with:  python -m pytest tests/test_indexer.py

A fake embedder keeps these tests fast and independent of the model download. It also records
what was embedded, which is how "unchanged courses are skipped" is proven."""
import copy
import hashlib

import pytest

from app.ingestion.indexer import get_collection, index_chunks, open_client, to_metadata

COLLECTION = "test_chunks"


class FakeEmbedder:
    def __init__(self):
        self.seen = []

    def __call__(self, texts):
        self.seen.extend(texts)
        return [[b / 255 for b in hashlib.sha1(t.encode()).digest()[:8]] for t in texts]


def _chunk(pdf, n, text=None, sha="sha-1"):
    text = text or f"{pdf} chunk {n}"
    return {"chunk_id": f"{pdf}::{n}", "course_name": pdf, "pdf_name": pdf, "page": 1, "page_end": 1, "section": "S",
            "chunk_type": "facts", "text": text, "body": text, "captured_on": None, "source_sha1": sha,
            "content_hash": hashlib.sha1(text.encode()).hexdigest()[:16]}


def _payload():
    return {"documents": [{"pdf_name": "A.pdf", "status": "OK"}, {"pdf_name": "B.pdf", "status": "OK"}],
            "chunks": [_chunk("A.pdf", 1), _chunk("A.pdf", 2), _chunk("A.pdf", 3), _chunk("B.pdf", 1), _chunk("B.pdf", 2)]}


@pytest.fixture
def client(tmp_path):
    return open_client(tmp_path / "chroma")


def _run(payload, client, embed=None, **kwargs):
    return index_chunks(payload, embed or FakeEmbedder(), client, collection_name=COLLECTION, **kwargs)


def test_first_run_stores_text_and_metadata(client):
    result = _run(_payload(), client)
    assert result.total == 5 and result.added == {"A.pdf": 3, "B.pdf": 2}
    stored = get_collection(client, COLLECTION).get(ids=["A.pdf::1"], include=["documents", "metadatas"])
    assert stored["documents"] == ["A.pdf chunk 1"]
    assert stored["metadatas"][0]["body"] == "A.pdf chunk 1"


def test_null_metadata_is_dropped():
    assert "captured_on" not in to_metadata(_chunk("A.pdf", 1))


def test_collection_uses_cosine_distance(client):
    _run(_payload(), client)
    assert get_collection(client, COLLECTION).metadata["hnsw:space"] == "cosine"


def test_reindexing_adds_nothing_and_embeds_nothing(client):
    _run(_payload(), client)
    embed = FakeEmbedder()
    result = _run(_payload(), client, embed)
    assert result.total == 5 and sorted(result.unchanged) == ["A.pdf", "B.pdf"]
    assert embed.seen == []


def test_changed_pdf_replaces_only_that_course(client):
    _run(_payload(), client)
    payload = _payload()
    payload["chunks"] = [_chunk("A.pdf", 1, "new text", sha="sha-2"), _chunk("B.pdf", 1), _chunk("B.pdf", 2)]
    embed = FakeEmbedder()
    result = _run(payload, client, embed)
    assert result.replaced == {"A.pdf": 1} and result.unchanged == ["B.pdf"]
    assert embed.seen == ["new text"], "B must not be embedded again"
    assert result.total == 3, "chunks that no longer exist must be removed"
    stored = get_collection(client, COLLECTION).get(ids=["A.pdf::1"], include=["documents"])
    assert stored["documents"] == ["new text"]


def test_rule_change_with_same_pdf_is_detected(client):
    _run(_payload(), client)
    payload = _payload()
    payload["chunks"][0] = _chunk("A.pdf", 1, "cleaned text")      # same PDF hash, different content
    result = _run(payload, client)
    assert result.replaced == {"A.pdf": 3}


def test_failed_pdf_keeps_its_old_chunks(client):
    _run(_payload(), client)
    payload = _payload()
    payload["documents"][0]["status"] = "FAIL"
    payload["chunks"] = [c for c in payload["chunks"] if c["pdf_name"] != "A.pdf"]
    result = _run(payload, client)
    assert result.kept_failed == ["A.pdf"] and result.total == 5
    assert result.orphaned == []


def test_rebuild_refuses_when_a_pdf_failed(client):
    _run(_payload(), client)
    payload = _payload()
    payload["documents"][0]["status"] = "FAIL"
    with pytest.raises(ValueError, match="Refusing to rebuild"):
        _run(payload, client, rebuild=True)
    assert get_collection(client, COLLECTION).count() == 5


def test_rebuild_removes_courses_no_longer_in_chunks(client):
    _run(_payload(), client)
    payload = _payload()
    payload["documents"] = payload["documents"][:1]
    payload["chunks"] = [c for c in payload["chunks"] if c["pdf_name"] == "A.pdf"]
    assert _run(copy.deepcopy(payload), client).orphaned == ["B.pdf"]
    assert _run(payload, client, rebuild=True).total == 3


def test_failed_embedding_leaves_the_index_untouched(client):
    _run(_payload(), client)
    payload = _payload()
    payload["chunks"][0] = _chunk("A.pdf", 1, "changed")

    def broken(_texts):
        raise RuntimeError("model crashed")

    with pytest.raises(RuntimeError):
        _run(payload, client, broken)
    with pytest.raises(RuntimeError):
        _run(payload, client, broken, rebuild=True)
    assert get_collection(client, COLLECTION).count() == 5
