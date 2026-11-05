"""The REST API, with a stand-in for retrieval and the LLM, so no index, key or model is needed."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, List, Optional

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config
from app.api.citations import strip_citations
from app.api.health import llm_is_ready
from app.api.main import UNAVAILABLE_REPLY, create_app
from app.api.sessions import SessionStore
from app.generation.llm import LLMError
from app.retrieval.retriever import RetrievalResult, RetrievedChunk, Route, load_rules

PY, JAVA = "The_Complete_Python_Bootcamp.pdf", "Java_Masterclass.pdf"
COURSES = {PY: "The Complete Python Bootcamp", JAVA: "Java Masterclass"}
SESSION, OTHER_SESSION = "session-a_1", "session-b_2"
SAFE_500 = "Something went wrong. Please try again."


class FakeCollection:
    def __init__(self, chunks: int = 304) -> None:
        self.chunks = chunks

    def count(self) -> int:
        return self.chunks


class FakeRetriever:
    """Finds the course by name, or falls back to the remembered one, as the real retriever does."""

    def __init__(self, error: Optional[Exception] = None, embed_error: Optional[Exception] = None) -> None:
        self.rules = load_rules()
        self.course_names = COURSES
        self.collection = FakeCollection()
        self.error = error
        self.embed_error = embed_error
        self.embedded: List[str] = []
        self.searched: List[Optional[str]] = []

    def embed(self, texts: List[str]) -> List[List[float]]:
        if self.embed_error:
            raise self.embed_error
        self.embedded += texts
        return [[0.0] for _ in texts]

    def retrieve(self, question: str, default_course: Optional[str] = None) -> RetrievalResult:
        if self.error:
            raise self.error
        if "france" in question.lower():
            return RetrievalResult(question=question, route=Route(), in_scope=False)
        named = PY if "python" in question.lower() else JAVA if "java" in question.lower() else None
        course = named or default_course
        self.searched.append(course)
        chunk = RetrievedChunk(chunk_id="c1", document=course, course_name=COURSES[course], page=2, page_end=3,
                               section="Facts", chunk_type="facts", similarity=0.7, text="passage")
        return RetrievalResult(question=question, route=Route(courses=[course]), chunks=[chunk])


class FakeLLM:
    def __init__(self, reply: str = "It takes 22 hours [1].", error: Optional[Exception] = None,
                 seconds: float = 0) -> None:
        self.reply = reply
        self.error = error
        self.seconds = seconds
        self.calls = 0
        self.running = 0
        self.most_running = 0

    def __call__(self, system: str, prompt: str) -> str:
        self.calls += 1
        self.running += 1
        self.most_running = max(self.most_running, self.running)
        time.sleep(self.seconds)
        self.running -= 1
        if self.error:
            raise self.error
        return self.reply


def _wait_for_startup(client: TestClient) -> None:
    deadline = time.monotonic() + 5
    while client.get("/api/v1/health").json()["warming"]:
        assert time.monotonic() < deadline, "the warm-up never finished"
        time.sleep(0.01)


@contextmanager
def running_api(retriever: Any = None, llm: Any = None, wait: bool = True, **options: Any) -> Iterator[TestClient]:
    settings = {"load_retriever": lambda: retriever or FakeRetriever(), "load_generator": lambda: llm or FakeLLM(),
                "check_llm": lambda: True, "startup_wait": 0, **options}
    with TestClient(create_app(**settings), raise_server_exceptions=False) as client:
        if wait:
            _wait_for_startup(client)
        yield client


def _ask(client: TestClient, message: str = "How long is the Python course?", session_id: str = SESSION) -> httpx.Response:
    return client.post("/api/v1/chat", json={"session_id": session_id, "message": message})


def _failing(error: Exception) -> Callable[[], Any]:
    def load() -> Any:
        raise error
    return load


# --- POST /api/v1/chat -----------------------------------------------------------------

def test_a_reply_has_exactly_the_agreed_shape() -> None:
    with running_api() as client:
        response = _ask(client)
    body = response.json()
    assert response.status_code == 200
    assert set(body) == {"session_id", "status", "answer", "cited_answer", "sources", "seconds"}
    assert body["session_id"] == SESSION and body["status"] == "answered"
    assert body["answer"] == "It takes 22 hours."
    assert body["cited_answer"] == "It takes 22 hours [1]."
    assert body["sources"] == [{"number": 1, "document": PY, "course_name": COURSES[PY], "page": 2, "page_end": 3,
                                "section": "Facts"}]
    assert isinstance(body["seconds"], float)


def test_student_id_is_optional_and_accepted() -> None:
    with running_api() as client:
        response = client.post("/api/v1/chat", json={"session_id": SESSION, "message": "Python?", "student_id": "STU101"})
    assert response.status_code == 200


@pytest.mark.parametrize("session_id", ["short77", "x" * 65, "has space1", "has.dot123", "semi;colon", "newline12\n", "pythön123"])
def test_a_bad_session_id_is_rejected(session_id: str) -> None:
    with running_api() as client:
        assert _ask(client, session_id=session_id).status_code == 422


@pytest.mark.parametrize("session_id", ["abc-DEF_", "x" * 64])
def test_a_good_session_id_is_accepted(session_id: str) -> None:
    with running_api() as client:
        assert _ask(client, session_id=session_id).status_code == 200


@pytest.mark.parametrize("message, detail", [
    ("   ", "The question is empty."),
    ("x" * (config.MAX_QUESTION_CHARS + 1), f"The question is too long. The limit is {config.MAX_QUESTION_CHARS} characters."),
])
def test_an_empty_or_long_question_gets_a_400(message: str, detail: str) -> None:
    with running_api() as client:
        response = _ask(client, message)
    assert response.status_code == 400 and response.json() == {"detail": detail}


def test_an_off_topic_question_is_a_normal_reply() -> None:
    llm = FakeLLM()
    with running_api(llm=llm) as client:
        body = _ask(client, "What is the capital of France?").json()
    assert body["status"] == "out_of_scope" and body["sources"] == [] and llm.calls == 0


def test_setup_details_never_reach_the_student_but_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    llm = FakeLLM(error=LLMError("The model is not installed. Run: ollama pull qwen3:1.7b"))
    with running_api(llm=llm) as client, caplog.at_level(logging.INFO, logger="app.api.main"):
        response = _ask(client)
    body = response.json()
    assert response.status_code == 200 and body["status"] == "unavailable"
    assert body["answer"] == body["cited_answer"] == UNAVAILABLE_REPLY
    assert "ollama" not in response.text.lower()
    assert "ollama pull" in caplog.text


def test_the_students_text_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    question = "How long is the Python course, private words?"
    with running_api() as client, caplog.at_level(logging.INFO):
        _ask(client, question)
    assert "private words" not in caplog.text
    assert f"chars={len(question)}" in caplog.text and "status=answered" in caplog.text


# --- Sessions through the API ----------------------------------------------------------

def test_two_sessions_do_not_share_the_remembered_course() -> None:
    retriever = FakeRetriever()
    with running_api(retriever=retriever) as client:
        _ask(client, "How long is the Python course?", SESSION)
        _ask(client, "How long is the Java course?", OTHER_SESSION)
        _ask(client, "How much does it cost?", SESSION)
        _ask(client, "How much does it cost?", OTHER_SESSION)
    assert retriever.searched == [PY, JAVA, PY, JAVA]


def test_session_limits_reach_the_store() -> None:
    retriever = FakeRetriever()
    with running_api(retriever=retriever, max_sessions=1) as client:
        _ask(client, "How long is the Python course?", SESSION)
        _ask(client, "How long is the Java course?", OTHER_SESSION)      # evicts SESSION
        _ask(client, "How long is the Python course?", SESSION)          # a fresh session, so nothing is remembered
    assert retriever.searched == [PY, JAVA, PY]


def test_only_one_model_call_runs_at_a_time() -> None:
    llm = FakeLLM(seconds=0.05)
    with running_api(llm=llm) as client, ThreadPoolExecutor(4) as pool:
        replies = list(pool.map(lambda n: _ask(client, session_id=f"session-{n}-x"), range(4)))
    assert [r.status_code for r in replies] == [200] * 4
    assert llm.calls == 4 and llm.most_running == 1


# --- Failures --------------------------------------------------------------------------

@pytest.mark.parametrize("error", [RuntimeError("secret C:\\path provider text"), ValueError("secret C:\\path provider text")])
def test_an_unexpected_failure_is_a_safe_500(error: Exception, caplog: pytest.LogCaptureFixture) -> None:
    with running_api(retriever=FakeRetriever(error=error)) as client, caplog.at_level(logging.ERROR):
        response = _ask(client)
    assert response.status_code == 500 and response.json() == {"detail": SAFE_500}
    assert "secret C:" in caplog.text


def test_a_failure_does_not_block_the_next_request() -> None:
    retriever = FakeRetriever(error=RuntimeError("boom"))
    with running_api(retriever=retriever) as client:
        assert _ask(client).status_code == 500
        retriever.error = None
        assert _ask(client).status_code == 200


def test_a_request_during_startup_gets_a_clean_503() -> None:
    release = threading.Event()

    def slow_retriever() -> FakeRetriever:
        release.wait(5)
        return FakeRetriever()

    with running_api(wait=False, load_retriever=slow_retriever) as client:
        response = _ask(client)
        health = client.get("/api/v1/health").json()
        release.set()
        _wait_for_startup(client)
        after = _ask(client)
    assert response.status_code == 503 and list(response.json()) == ["detail"]
    assert health["warming"] is True and health["status"] == "degraded"
    assert after.status_code == 200


def test_a_request_during_startup_can_wait_for_it() -> None:
    def slow_retriever() -> FakeRetriever:
        time.sleep(0.1)
        return FakeRetriever()

    with running_api(wait=False, load_retriever=slow_retriever, startup_wait=5) as client:
        assert _ask(client).status_code == 200


@pytest.mark.parametrize("broken", ["load_retriever", "load_generator"])
def test_a_failed_start_is_degraded_and_the_page_still_loads(broken: str, tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>Chat</h1>", encoding="utf-8")
    secret = "secret path text"
    with running_api(web_dir=tmp_path, **{broken: _failing(RuntimeError(secret))}) as client:
        chat, health, page = _ask(client), client.get("/api/v1/health"), client.get("/")
    assert chat.status_code == 503 and list(chat.json()) == ["detail"] and secret not in chat.text
    assert health.status_code == 200 and health.json()["status"] == "degraded" and secret not in health.text
    assert page.status_code == 200


# --- Warm-up ---------------------------------------------------------------------------

def test_warm_up_embeds_and_loads_the_local_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    retriever, llm = FakeRetriever(), FakeLLM()
    with running_api(retriever=retriever, llm=llm):
        pass
    assert len(retriever.embedded) == 1 and llm.calls == 1


def test_warm_up_does_not_spend_the_gemini_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    retriever, llm = FakeRetriever(), FakeLLM()
    with running_api(retriever=retriever, llm=llm):
        pass
    assert len(retriever.embedded) == 1 and llm.calls == 0


def test_a_failed_warm_up_never_stops_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    with running_api(llm=FakeLLM(error=LLMError("Could not reach Ollama"))) as client:
        health = client.get("/api/v1/health")
    assert health.status_code == 200 and health.json()["warming"] is False


# --- GET /api/v1/health ----------------------------------------------------------------

def test_health_when_everything_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "LLM_MODEL", "qwen3:1.7b")
    with running_api() as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "provider": "ollama", "model": "qwen3:1.7b", "llm_ready": True,
                               "index_chunks": 304, "courses": 2, "warming": False}


def test_health_names_the_gemini_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-test")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "key-that-must-stay-hidden")
    with running_api() as client:
        response = client.get("/api/v1/health")
    assert response.json()["model"] == "gemini-test"
    assert "key-that-must-stay-hidden" not in response.text


def test_health_is_degraded_when_the_model_is_not_ready() -> None:
    with running_api(check_llm=lambda: False) as client:
        body = client.get("/api/v1/health").json()
    assert body["status"] == "degraded" and body["llm_ready"] is False


def test_health_survives_a_failing_check() -> None:
    with running_api(check_llm=_failing(RuntimeError("boom"))) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded" and response.json()["llm_ready"] is False


def test_health_survives_a_broken_index() -> None:
    retriever = FakeRetriever()
    with running_api(retriever=retriever) as client:
        retriever.collection = None
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded" and response.json()["index_chunks"] == 0


def _ollama_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))


def _tags(*names: str) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": name} for name in names]})
    return handler


def _no_connection(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused")


@pytest.mark.parametrize("model, installed, ready", [
    ("qwen3:1.7b", ["gemma3:1b", "qwen3:1.7b"], True),
    ("qwen3", ["qwen3:latest"], True),
    ("qwen3:1.7b", ["qwen3:4b"], False),
    ("qwen3:1.7b", [], False),
])
def test_ollama_is_ready_only_with_the_model_installed(model: str, installed: List[str], ready: bool,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "LLM_MODEL", model)
    assert llm_is_ready(_ollama_client(_tags(*installed))) is ready


@pytest.mark.parametrize("handler", [_no_connection, lambda request: httpx.Response(500),
                                     lambda request: httpx.Response(200, text="not json"),
                                     lambda request: httpx.Response(200, json=["odd"])])
def test_ollama_is_not_ready_when_it_cannot_be_determined(handler: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    assert llm_is_ready(_ollama_client(handler)) is False


@pytest.mark.parametrize("key, ready", [("a-key", True), ("", False)])
def test_gemini_is_ready_with_a_key(key: str, ready: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "GEMINI_API_KEY", key)
    assert llm_is_ready() is ready


def test_an_unknown_provider_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LLM_PROVIDER", "other")
    assert llm_is_ready() is False


# --- The page --------------------------------------------------------------------------

def test_the_page_and_its_files_are_served(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>Chat</h1>", encoding="utf-8")
    (tmp_path / "app.js").write_text("let a = 1;", encoding="utf-8")
    with running_api(web_dir=tmp_path) as client:
        page, script = client.get("/"), client.get("/web/app.js")
    assert page.status_code == 200 and "<h1>Chat</h1>" in page.text and "text/html" in page.headers["content-type"]
    assert script.status_code == 200 and script.text == "let a = 1;"


def test_the_server_starts_without_the_web_folder(tmp_path: Path) -> None:
    with running_api(web_dir=tmp_path / "missing") as client:
        page, script, health = client.get("/"), client.get("/web/app.js"), client.get("/api/v1/health")
    assert page.status_code == 200 and "text/plain" in page.headers["content-type"]
    assert script.status_code == 404
    assert health.status_code == 200


# --- Removing citation markers from the student's text ---------------------------------

@pytest.mark.parametrize("cited, clean", [
    ("It takes 22 hours [1].", "It takes 22 hours."),
    ("Fact one [1] and fact two [2].", "Fact one and fact two."),
    ("It takes 22 hours [1][2].", "It takes 22 hours."),
    ("It takes 22 hours [1] [2] .", "It takes 22 hours."),
    ("It takes 22 hours. [1]", "It takes 22 hours."),
    ("It takes 22 hours [1]. [2].", "It takes 22 hours."),
    ("Price [1], and rating [2]; both shown.", "Price, and rating; both shown."),
])
def test_markers_and_their_leftover_spacing_are_removed(cited: str, clean: str) -> None:
    assert strip_citations(cited, [1, 2]) == clean


def test_brackets_in_course_names_stay() -> None:
    cited = "The Complete Python Bootcamp [2026] [NEW] costs 499 [1]."
    assert strip_citations(cited, [1]) == "The Complete Python Bootcamp [2026] [NEW] costs 499."


def test_a_number_that_is_not_a_cited_source_stays() -> None:
    assert strip_citations("Volume [3] of the notes [1].", [1]) == "Volume [3] of the notes."


def test_a_reply_with_no_sources_is_left_exactly_as_it_is() -> None:
    text = "These are the courses:\n- Python  [1] Bootcamp ."
    assert strip_citations(text, []) == text


def test_line_breaks_and_list_lines_are_kept() -> None:
    cited = "You need:\n- A computer [1]\n  - With internet [2]\n\nThat is all [1]."
    assert strip_citations(cited, [1, 2]) == "You need:\n- A computer\n  - With internet\n\nThat is all."


def test_a_line_holding_only_citations_does_not_leave_a_gap() -> None:
    assert strip_citations("First.\n\n[1]\n\nSecond [2].", [1, 2]) == "First.\n\nSecond."


def test_a_dot_starting_a_word_and_an_ellipsis_are_not_touched() -> None:
    assert strip_citations("It covers .NET and more... [1]", [1]) == "It covers .NET and more..."


# --- Sessions --------------------------------------------------------------------------

class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _store(max_sessions: int = 3, ttl_seconds: float = 60, clock: Clock = None) -> SessionStore:
    return SessionStore(create=object, max_sessions=max_sessions, ttl_seconds=ttl_seconds, clock=clock or Clock())


def test_a_session_keeps_its_own_object() -> None:
    store = _store()
    assert store.get("a") is store.get("a")
    assert store.get("a") is not store.get("b")


def test_the_oldest_session_is_evicted_when_the_store_is_full() -> None:
    store = _store(max_sessions=2)
    first = store.get("a")
    store.get("b")
    store.get("c")
    assert len(store) == 2
    assert store.get("a") is not first


def test_using_a_session_makes_it_the_newest() -> None:
    store = _store(max_sessions=2)
    first = store.get("a")
    store.get("b")
    store.get("a")
    store.get("c")                                  # evicts b, not a
    assert store.get("a") is first


def test_an_idle_session_expires() -> None:
    clock = Clock()
    store = _store(ttl_seconds=60, clock=clock)
    first = store.get("a")
    clock.now = 61
    assert store.get("a") is not first


def test_a_session_in_use_does_not_expire() -> None:
    clock = Clock()
    store = _store(ttl_seconds=60, clock=clock)
    first = store.get("a")
    clock.now = 50
    store.get("a")
    clock.now = 100
    assert store.get("a") is first


def test_expired_sessions_are_dropped_when_another_session_arrives() -> None:
    clock = Clock()
    store = _store(ttl_seconds=60, clock=clock)
    store.get("a")
    store.get("b")
    clock.now = 61
    store.get("c")
    assert len(store) == 1
