"""Answers one question for GET /api/v1/health: can the configured model answer right now?"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from app import config

log = logging.getLogger(__name__)


def model_name() -> str:
    return config.GEMINI_MODEL if config.LLM_PROVIDER == "gemini" else config.LLM_MODEL


def _ollama_has_model(client: httpx.Client) -> bool:
    try:
        response = client.get("/api/tags")
        response.raise_for_status()
        installed = {model["name"] for model in response.json()["models"]}
    except Exception as exc:        # not running, too slow, or a reply that is not Ollama's
        log.info("Could not check Ollama: %s", type(exc).__name__)
        return False
    return config.LLM_MODEL in installed or f"{config.LLM_MODEL}:latest" in installed


def llm_is_ready(client: Optional[httpx.Client] = None) -> bool:
    if config.LLM_PROVIDER == "gemini":
        return bool(config.GEMINI_API_KEY)
    if config.LLM_PROVIDER != "ollama":
        return False
    if client is not None:
        return _ollama_has_model(client)
    # trust_env is off for the same reason as in OllamaGenerator: a Windows proxy must not be used.
    with httpx.Client(base_url=config.LLM_BASE_URL, timeout=config.HEALTH_TIMEOUT_SECONDS, trust_env=False) as own:
        return _ollama_has_model(own)
