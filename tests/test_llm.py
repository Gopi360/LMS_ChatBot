"""The LLM wrappers: what they send, and how they report each kind of failure."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Callable, Optional

import httpx
import pytest
from google.genai import errors, types

from app import config
from app.generation.llm import GeminiGenerator, LLMError, OllamaGenerator, get_generator

SECRET = "not-a-real-key-123"


class FakeModels:
    def __init__(self, text: Optional[str] = "An answer [1].", error: Optional[Exception] = None,
                 finish: Any = "STOP") -> None:
        self.text = text
        self.error = error
        self.finish = finish
        self.request: dict = {}

    def generate_content(self, **request: Any) -> SimpleNamespace:
        self.request = request
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text, candidates=[SimpleNamespace(finish_reason=self.finish)])


def _generator(models: FakeModels) -> GeminiGenerator:
    return GeminiGenerator(SECRET, "gemini-test", client=SimpleNamespace(models=models))


def test_the_reply_text_is_returned_without_extra_space() -> None:
    assert _generator(FakeModels("  An answer [1].\n"))("system", "prompt") == "An answer [1]."


def test_the_request_carries_the_model_rules_and_low_randomness() -> None:
    models = FakeModels()
    _generator(models)("the rules", "the prompt")
    assert models.request["model"] == "gemini-test" and models.request["contents"] == "the prompt"
    assert models.request["config"].system_instruction == "the rules"
    assert models.request["config"].temperature == config.LLM_TEMPERATURE


@pytest.mark.parametrize("error, phrase", [
    (errors.ClientError(429, {"error": {"message": "quota"}}), "limit"),
    (errors.ClientError(400, {"error": {"message": "API key not valid"}}), "key"),
    (errors.ClientError(403, {"error": {"message": "denied"}}), "key"),
    (errors.ClientError(404, {"error": {"message": "no such model"}}), "model"),
    (errors.ServerError(503, {"error": {"message": "overloaded"}}), "busy"),
    (httpx.ConnectError("no network"), "internet"),
    (httpx.ReadTimeout("slow"), "internet"),
    (errors.APIError(302, {"error": {"message": "moved"}}), "Gemini"),
    (json.JSONDecodeError("not json", "<html>", 0), "Gemini"),
    (UnicodeEncodeError("ascii", "x", 0, 1, "bad"), "Gemini"),
])
def test_each_failure_gets_a_clear_message(error: Exception, phrase: str) -> None:
    with pytest.raises(LLMError, match=phrase):
        _generator(FakeModels(error=error))("system", "prompt")


@pytest.mark.parametrize("text", [None, "", "   "])
def test_an_empty_reply_is_a_failure(text: Optional[str]) -> None:
    with pytest.raises(LLMError):
        _generator(FakeModels(text))("system", "prompt")


@pytest.mark.parametrize("finish", ["MAX_TOKENS", "RECITATION", types.FinishReason.MAX_TOKENS])
def test_a_reply_that_stopped_early_is_a_failure(finish: Any) -> None:
    with pytest.raises(LLMError, match="cut short"):
        _generator(FakeModels("Half an answer [1] and", finish=finish))("system", "prompt")


def test_a_normal_finish_from_the_real_enum_is_fine() -> None:
    assert _generator(FakeModels(finish=types.FinishReason.STOP))("system", "prompt") == "An answer [1]."


def test_the_key_never_appears_in_an_error() -> None:
    error = errors.ClientError(400, {"error": {"message": f"bad key {SECRET}"}})
    with pytest.raises(LLMError) as caught:
        _generator(FakeModels(error=error))("system", "prompt")
    assert SECRET not in str(caught.value)


def test_a_missing_key_says_how_to_fix_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        get_generator("gemini")


def test_an_unknown_provider_is_rejected() -> None:
    with pytest.raises(LLMError, match="LLM_PROVIDER"):
        get_generator("something-else")


# --- Ollama. Replies copy what Ollama 0.40 really returned; only the network is faked. ---------

def _ollama(handler: Callable[[httpx.Request], httpx.Response]) -> OllamaGenerator:
    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    return OllamaGenerator("test-model", client=client)


def _chat_reply(content: str = "An answer [1].", done_reason: str = "stop") -> httpx.Response:
    return httpx.Response(200, json={
        "model": "test-model", "created_at": "2026-11-05T06:38:38.7664373Z",
        "message": {"role": "assistant", "content": content}, "done": True, "done_reason": done_reason,
        "total_duration": 5413998000, "load_duration": 4593275600, "prompt_eval_count": 21,
        "prompt_eval_duration": 424571000, "eval_count": 3, "eval_duration": 135156000})


def test_ollama_returns_the_reply_without_extra_space() -> None:
    assert _ollama(lambda request: _chat_reply("  An answer [1].\n"))("system", "prompt") == "An answer [1]."


def test_ollama_request_sends_the_rules_question_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LLM_TEMPERATURE", 0.2)
    monkeypatch.setattr(config, "NUM_CTX", 4096)
    monkeypatch.setattr(config, "LLM_NUM_PREDICT", 256)
    monkeypatch.setattr(config, "LLM_KEEP_ALIVE", "30m")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(path=request.url.path, body=json.loads(request.content))
        return _chat_reply()

    _ollama(handler)("the rules", "the prompt")
    assert seen["path"] == "/api/chat"
    assert seen["body"] == {
        "model": "test-model", "stream": False, "think": False, "keep_alive": "30m",
        "messages": [{"role": "system", "content": "the rules"}, {"role": "user", "content": "the prompt"}],
        "options": {"temperature": 0.2, "presence_penalty": 0, "num_ctx": 4096, "num_predict": 256}}


def _raises(error: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error
    return handler


@pytest.mark.parametrize("handler, phrase", [
    (_raises(httpx.ConnectError("refused")), "Is Ollama running"),
    (_raises(httpx.ReadTimeout("slow")), "too long"),
    (lambda request: httpx.Response(404, json={"error": "model 'test-model' not found"}), "ollama pull test-model"),
    (lambda request: httpx.Response(500, json={"error": "out of memory"}), "could not answer"),
    (lambda request: httpx.Response(404, text="404 page not found"), "could not answer"),
    (lambda request: httpx.Response(200, text="<html>a proxy page</html>"), "could not answer"),
    (lambda request: httpx.Response(200, json=["not", "an", "object"]), "no answer"),
])
def test_ollama_failures_get_a_clear_message(handler: Callable[[httpx.Request], httpx.Response], phrase: str) -> None:
    with pytest.raises(LLMError, match=phrase):
        _ollama(handler)("system", "prompt")


def test_ollama_never_shows_its_own_error_text() -> None:
    with pytest.raises(LLMError) as caught:
        _ollama(lambda request: httpx.Response(500, json={"error": "C:/Users/secret/path failed"}))("system", "prompt")
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("content", ["", "   "])
def test_ollama_empty_reply_is_a_failure(content: str) -> None:
    with pytest.raises(LLMError, match="no answer"):
        _ollama(lambda request: _chat_reply(content))("system", "prompt")


def test_ollama_reply_without_a_message_is_a_failure() -> None:
    with pytest.raises(LLMError, match="no answer"):
        _ollama(lambda request: httpx.Response(200, json={"done": True}))("system", "prompt")


def test_ollama_reply_that_hit_the_length_limit_is_a_failure() -> None:
    with pytest.raises(LLMError, match="cut short"):
        _ollama(lambda request: _chat_reply("Half an answer [1] and", "length"))("system", "prompt")


def test_ollama_needs_no_gemini_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "LLM_MODEL", "some-model:1b")
    generator = get_generator("ollama")
    assert isinstance(generator, OllamaGenerator) and generator.model == "some-model:1b"


def test_ollama_uses_the_configured_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://other-machine:9999")
    assert str(OllamaGenerator("test-model").client.base_url).startswith("http://other-machine:9999")


@pytest.mark.parametrize("setting, sent", [("30m", "30m"), ("3600", 3600), ("-1", -1)])
def test_ollama_sends_keep_alive_in_a_form_it_accepts(monkeypatch: pytest.MonkeyPatch, setting: str, sent: Any) -> None:
    monkeypatch.setattr(config, "LLM_KEEP_ALIVE", setting)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return _chat_reply()

    _ollama(handler)("system", "prompt")
    assert seen["keep_alive"] == sent


def test_ollama_refuses_a_prompt_it_would_cut_without_sending_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "NUM_CTX", 300)
    monkeypatch.setattr(config, "LLM_NUM_PREDICT", 100)
    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _chat_reply()

    with pytest.raises(LLMError, match="too much"):
        _ollama(handler)("system", "x" * 1000)
    assert calls == []


def test_ollama_accepts_a_prompt_that_fits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "NUM_CTX", 300)
    monkeypatch.setattr(config, "LLM_NUM_PREDICT", 100)
    assert _ollama(lambda request: _chat_reply())("system", "x" * 500) == "An answer [1]."


def test_ollama_logs_its_error_text_for_the_operator(caplog: pytest.LogCaptureFixture) -> None:
    with pytest.raises(LLMError):
        _ollama(lambda request: httpx.Response(500, json={"error": "needs more system memory"}))("system", "prompt")
    assert "needs more system memory" in caplog.text


def test_ollama_ignores_system_proxy_settings() -> None:
    assert OllamaGenerator("test-model").client.trust_env is False
