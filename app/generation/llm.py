"""Calls the LLM that writes the answer. The provider is a setting (LLM_PROVIDER): ollama runs a
model on this machine, gemini uses Google's service. A paid service can be added later with one
class here. Nothing else in the project depends on the provider: it only sees a Generator, a
function taking the rules and the prompt.

The Gemini free tier is for the demo only. Google may use free-tier prompts to improve its
products and people may read them, so real course PDFs and student data must not go through it.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Union

import httpx
from google import genai
from google.genai import errors, types

from app import config

log = logging.getLogger(__name__)

Generator = Callable[[str, str], str]
NETWORK_ERRORS = (httpx.TransportError, ConnectionError, TimeoutError)


class LLMError(RuntimeError):
    """A failure whose message is safe to show: it never contains the key or the provider's text."""


class GeminiGenerator:
    def __init__(self, api_key: str, model: str, client: Optional[Any] = None) -> None:
        self.model = model
        self.client = client or genai.Client(api_key=api_key, http_options=types.HttpOptions(
            timeout=config.LLM_TIMEOUT_SECONDS * 1000,                      # milliseconds
            retry_options=types.HttpRetryOptions(attempts=2, http_status_codes=[500, 502, 503, 504])))

    def __call__(self, system: str, prompt: str) -> str:
        request = types.GenerateContentConfig(
            system_instruction=system, temperature=config.LLM_TEMPERATURE, max_output_tokens=config.LLM_MAX_OUTPUT_TOKENS,
            thinking_config=types.ThinkingConfig(thinking_level=config.GEMINI_THINKING_LEVEL))
        try:
            response = self.client.models.generate_content(model=self.model, contents=prompt, config=request)
        except errors.ClientError as exc:
            log.error("Gemini refused the request: HTTP %s", exc.code)
            raise LLMError(_client_error_message(exc.code)) from None
        except errors.ServerError as exc:
            log.error("Gemini server error: HTTP %s", exc.code)
            raise LLMError("Gemini is busy right now. Please try again in a moment.") from None
        except NETWORK_ERRORS as exc:
            log.error("Could not reach Gemini: %s", type(exc).__name__)
            raise LLMError("Could not reach Gemini. Check the internet connection and try again.") from None
        except Exception as exc:        # the SDK has other failures: a proxy page, an odd status, a bad key
            log.error("Gemini call failed: %s", type(exc).__name__)
            raise LLMError("Gemini could not answer right now. Please try again.") from None

        text = (response.text or "").strip()
        reason = response.candidates[0].finish_reason if response.candidates else None
        reason = getattr(reason, "name", reason)
        if not text:
            log.error("Gemini returned no text: %s", reason)
            raise LLMError("Gemini returned no answer. Please try again.")
        if reason not in (None, "STOP"):
            log.error("Gemini stopped early: %s", reason)
            raise LLMError("The answer was cut short. Please ask about a smaller part of the course.")
        return text


def _client_error_message(code: int) -> str:
    if code == 429:
        return "The Gemini free limit has been reached. Wait a minute, or try again tomorrow if the daily limit is used up."
    if code == 404:
        return f"Gemini does not offer the model {config.GEMINI_MODEL}. Check GEMINI_MODEL in .env."
    if code in (400, 401, 403):
        return "Gemini rejected the request. Check GEMINI_API_KEY in .env and that this key can use the model."
    return f"Gemini refused the request (HTTP {code})."


class OllamaGenerator:
    """A model on this machine, through Ollama's HTTP API, so the passages never leave it."""

    def __init__(self, model: str, client: Optional[httpx.Client] = None) -> None:
        self.model = model
        # trust_env is off because a Windows proxy setting could otherwise carry the passages off this machine.
        self.client = client or httpx.Client(base_url=config.LLM_BASE_URL, timeout=config.OLLAMA_TIMEOUT_SECONDS,
                                             trust_env=False)

    def __call__(self, system: str, prompt: str) -> str:
        # Ollama cuts a prompt that is too long, reports success, and its token count hides the cut.
        # So check first. Three characters per token is a cautious estimate for English.
        if (len(system) + len(prompt)) // 3 > config.NUM_CTX - config.LLM_NUM_PREDICT:
            log.error("The prompt is too long for NUM_CTX=%s", config.NUM_CTX)
            raise LLMError("That needs too much course text at once. Please ask about one course at a time.")
        request = {
            "model": self.model, "stream": False, "think": False, "keep_alive": _keep_alive(config.LLM_KEEP_ALIVE),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "options": {"temperature": config.LLM_TEMPERATURE, "presence_penalty": 0, "num_ctx": config.NUM_CTX,
                        "num_predict": config.LLM_NUM_PREDICT}}
        try:
            response = self.client.post("/api/chat", json=request)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException:
            log.error("Ollama timed out after %s s", config.OLLAMA_TIMEOUT_SECONDS)
            raise LLMError("The local model took too long to answer. Please try again.") from None
        except httpx.TransportError as exc:
            log.error("Could not reach Ollama: %s", type(exc).__name__)
            raise LLMError("Could not reach the local model. Is Ollama running?") from None
        except httpx.HTTPStatusError as exc:
            log.error("Ollama refused the request: HTTP %s: %s", exc.response.status_code, exc.response.text[:200])
            if exc.response.status_code == 404 and "model" in exc.response.text:     # a wrong address is also a 404
                raise LLMError(f"The model is not installed. Run: ollama pull {self.model}") from None
            raise LLMError("The local model could not answer right now. Please try again.") from None
        except Exception as exc:        # a reply that is not JSON, such as a proxy page
            log.error("Ollama call failed: %s", type(exc).__name__)
            raise LLMError("The local model could not answer right now. Please try again.") from None

        message = data.get("message") if isinstance(data, dict) else None
        text = (message.get("content") or "").strip() if isinstance(message, dict) else ""
        if not text:
            log.error("Ollama returned no text")
            raise LLMError("The local model returned no answer. Please try again.")
        if data.get("done_reason") == "length":
            log.error("Ollama stopped at the limit of %s tokens", config.LLM_NUM_PREDICT)
            raise LLMError("The answer was cut short. Please ask about a smaller part of the course.")
        return text


def _keep_alive(value: str) -> Union[int, str]:
    """Ollama accepts 3600 or "30m", but rejects "3600", and settings always arrive as text."""
    try:
        return int(value)
    except ValueError:
        return value


def get_generator(provider: Optional[str] = None) -> Generator:
    provider = provider or config.LLM_PROVIDER
    if provider == "ollama":
        return OllamaGenerator(config.LLM_MODEL)
    if provider != "gemini":
        raise LLMError(f"LLM_PROVIDER '{provider}' is not supported. Use: ollama or gemini.")
    if not config.GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY is not set. Add it to the .env file (see .env.example).")
    return GeminiGenerator(config.GEMINI_API_KEY, config.GEMINI_MODEL)
