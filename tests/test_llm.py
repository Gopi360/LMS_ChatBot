"""The Gemini wrapper: what it sends, and how it reports each kind of failure."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Optional

import httpx
import pytest
from google.genai import errors, types

from app import config
from app.generation.llm import GeminiGenerator, LLMError, get_generator

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
