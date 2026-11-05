"""The REST API and the demo page.

    python -m app.api.main          # or run_demo.bat, which also selects the local model

The port opens at once. The index and the models load in a background thread, and /api/v1/health
shows when that is done. Each session has its own Chatbot, because a Chatbot remembers the course being
discussed, but they share one retriever and one generator. One lock runs one question at a time:
a Chatbot is not thread safe, and this laptop can only run one model call anyway.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from app import config
from app.api.citations import strip_citations
from app.api.health import llm_is_ready, model_name
from app.api.schemas import ChatRequest, ChatResponse, HealthResponse
from app.api.sessions import SessionStore
from app.generation.chatbot import ChatReply, Chatbot
from app.generation.llm import get_generator
from app.retrieval.retriever import get_retriever

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

BUSY_DETAIL = "The assistant is busy with another question. Please try again."
UNAVAILABLE_REPLY = "The assistant is unavailable right now. Please try again in a moment."
STARTING_DETAIL = "The assistant is still starting. Please try again in a moment."
NOT_RUNNING_DETAIL = "The assistant is not available right now. Please try again later."
ERROR_DETAIL = "Something went wrong. Please try again."
NO_PAGE_MESSAGE = "The chat page is not installed. The API is running: see /docs."


class Runtime:
    """What is built once and shared by every session."""

    def __init__(self, load_retriever: Callable[[], Any], load_generator: Callable[[], Any],
                 max_sessions: int, ttl_seconds: float) -> None:
        self.load_retriever = load_retriever
        self.load_generator = load_generator
        self.retriever: Any = None
        self.generator: Any = None
        self.sessions = SessionStore(lambda: Chatbot(self.retriever, self.generator), max_sessions, ttl_seconds)
        self.lock = threading.Lock()
        self.loaded = threading.Event()         # the loading finished, whether or not it worked
        self.warming = True

    @property
    def running(self) -> bool:
        return self.retriever is not None and self.generator is not None

    def start(self) -> None:
        threading.Thread(target=self._start, name="chatbot-startup", daemon=True).start()

    def _start(self) -> None:
        self.generator = self._load("model provider", self.load_generator)
        self.retriever = self._load("index", self.load_retriever)
        self.loaded.set()
        try:
            self._warm_up()
        except Exception:
            log.exception("The warm-up failed. The first question will be slower")
        finally:
            self.warming = False
            log.info("Startup finished")

    @staticmethod
    def _load(name: str, load: Callable[[], Any]) -> Any:
        try:
            return load()
        except Exception:
            log.exception("Could not start the %s", name)
            return None

    def _warm_up(self) -> None:
        if not self.running:
            return
        with self.lock:
            self.retriever.embed(["warm up"])
            matcher = getattr(self.retriever, "matcher", None)
            if matcher:                             # it embeds its example questions on first use
                matcher.best_score("warm up")
            if config.LLM_PROVIDER == "ollama":     # never Gemini: it would spend the free quota
                self.generator("Reply with one word.", "Say OK.")

    def index_size(self) -> Tuple[int, int]:
        if self.retriever is None:
            return 0, 0
        try:
            return self.retriever.collection.count(), len(self.retriever.course_names)
        except Exception as exc:
            log.error("Could not read the index: %s", type(exc).__name__)
            return 0, 0


def _is_invalid(question: str, bot: Chatbot) -> bool:
    question = question.strip()
    return not question or len(question) > bot.max_question_chars


def _ask(runtime: Runtime, session_id: str, question: str) -> ChatReply:
    if not runtime.lock.acquire(timeout=config.CHAT_QUEUE_WAIT_SECONDS):
        log.warning("A question waited too long behind another one")
        raise HTTPException(status_code=503, detail=BUSY_DETAIL)
    try:
        bot = runtime.sessions.get(session_id)
        try:
            return bot.ask(question)
        except ValueError as exc:
            if _is_invalid(question, bot):      # only then is the message the chatbot's own safe one
                log.info("chat rejected chars=%s", len(question))
                raise HTTPException(status_code=400, detail=str(exc)) from None
            raise
    finally:
        runtime.lock.release()


def _to_response(session_id: str, reply: ChatReply, seconds: float) -> ChatResponse:
    if reply.status == "unavailable":           # the text can hold setup details, such as "Run: ollama pull"
        log.error("The model could not answer: %s", reply.text)
        answer = cited_answer = UNAVAILABLE_REPLY
    else:
        answer, cited_answer = strip_citations(reply.text, [s.number for s in reply.sources]), reply.text
    return ChatResponse(session_id=session_id, status=reply.status, answer=answer, cited_answer=cited_answer,
                        sources=[asdict(s) for s in reply.sources], seconds=round(seconds, 1))


def _safe_llm_check(check_llm: Callable[[], bool]) -> bool:
    try:
        return bool(check_llm())
    except Exception as exc:
        log.error("The model check failed: %s", type(exc).__name__)
        return False


def create_app(load_retriever: Callable[[], Any] = get_retriever, load_generator: Callable[[], Any] = get_generator,
               check_llm: Callable[[], bool] = llm_is_ready, web_dir: Path = WEB_DIR,
               startup_wait: Optional[float] = None, max_sessions: Optional[int] = None,
               ttl_minutes: Optional[float] = None) -> FastAPI:
    wait = config.CHAT_STARTUP_WAIT_SECONDS if startup_wait is None else startup_wait
    runtime = Runtime(load_retriever, load_generator,
                      config.CHAT_MAX_SESSIONS if max_sessions is None else max_sessions,
                      (config.CHAT_SESSION_TTL_MINUTES if ttl_minutes is None else ttl_minutes) * 60)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
        logging.getLogger("app").setLevel(logging.INFO)
        runtime.start()
        yield

    app = FastAPI(title="LMS Course Chatbot", lifespan=lifespan)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        log.error("Unexpected failure on %s", request.url.path, exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": ERROR_DETAIL})

    # A sync def, so FastAPI runs it in its thread pool and a slow answer never blocks /health.
    @app.post("/api/v1/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        if not runtime.loaded.wait(wait):
            raise HTTPException(status_code=503, detail=STARTING_DETAIL)
        if not runtime.running:
            raise HTTPException(status_code=503, detail=NOT_RUNNING_DETAIL)
        reply = _ask(runtime, request.session_id, request.message)
        seconds = time.perf_counter() - started
        log.info("chat status=%s chars=%s seconds=%.1f", reply.status, len(request.message), seconds)
        return _to_response(request.session_id, reply, seconds)

    @app.get("/api/v1/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        llm_ready = _safe_llm_check(check_llm)
        chunks, courses = runtime.index_size()
        ok = llm_ready and chunks > 0 and runtime.running
        return HealthResponse(status="ok" if ok else "degraded", provider=config.LLM_PROVIDER, model=model_name(),
                              llm_ready=llm_ready, index_chunks=chunks, courses=courses, warming=runtime.warming)

    @app.get("/", include_in_schema=False)
    def page() -> Response:
        index = web_dir / "index.html"
        return FileResponse(index) if index.is_file() else PlainTextResponse(NO_PAGE_MESSAGE)

    if web_dir.is_dir():                        # StaticFiles fails on a missing folder
        app.mount("/web", StaticFiles(directory=web_dir), name="web")
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.API_HOST, port=config.API_PORT)
