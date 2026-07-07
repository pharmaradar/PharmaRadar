"""Multi-provider LLM router via LiteLLM.

Supported providers:
  vertex      – Vertex AI (Gemini 2.5 Pro / Flash)
  openrouter  – OpenRouter (any model)
  ollama      – Ollama local inference
  nvidia      – NVIDIA NIM (OpenAI-compatible)
  anthropic   – Anthropic (Claude)
  openai      – OpenAI
  gemini      – Google AI Studio (Gemini via API key)

Provider + model are read from AppSettings (DB) at call time, not at import time,
so changing settings takes effect on the next LLM call without a restart.
"""
from __future__ import annotations

import os
from typing import Any

import structlog
from litellm import completion, RateLimitError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import get_settings
from app.models.app_settings import PROVIDERS

logger = structlog.get_logger(__name__)
_config = get_settings()


# ── Provider → LiteLLM model string builder ───────────────

def _model_string(provider: str, model: str, base_url: str | None = None) -> str:
    mapping = {
        "vertex":     f"vertex_ai/{model}",
        "openrouter": f"openrouter/{model}",
        "ollama":     f"ollama/{model}",
        "nvidia":     f"nvidia_nim/{model}",
        "anthropic":  model if model.startswith("claude") else f"claude-{model}",
        "openai":     model,
        "gemini":     f"gemini/{model}",
    }
    return mapping.get(provider, model)


def _extra_kwargs(provider: str, settings_row) -> dict[str, Any]:
    """Build provider-specific kwargs for litellm.completion(). Keys read from config (.env)."""
    kwargs: dict[str, Any] = {}

    if provider == "vertex":
        kwargs["vertex_project"] = _config.google_cloud_project
        kwargs["vertex_location"] = _config.google_cloud_location

    elif provider == "openrouter":
        kwargs["api_key"] = _config.openrouter_api_key
        kwargs["api_base"] = "https://openrouter.ai/api/v1"

    elif provider == "ollama":
        base = (settings_row.ollama_base_url if settings_row else None) or "http://localhost:11434"
        kwargs["api_base"] = base
        kwargs["api_key"] = "ollama"   # litellm requires a non-empty string

    elif provider == "nvidia":
        base = (settings_row.nvidia_base_url if settings_row else None) or "https://integrate.api.nvidia.com/v1"
        kwargs["api_key"] = _config.nvidia_api_key
        kwargs["api_base"] = base

    elif provider == "anthropic":
        kwargs["api_key"] = _config.anthropic_api_key

    elif provider == "openai":
        kwargs["api_key"] = _config.openai_api_key
        if settings_row and settings_row.custom_base_url:
            kwargs["api_base"] = settings_row.custom_base_url

    elif provider == "gemini":
        kwargs["api_key"] = _config.gemini_api_key

    return kwargs


# ── DB settings loader (sync) ─────────────────────────────

def _load_settings():
    """Load AppSettings synchronously — called from sync Celery context."""
    import asyncio
    from app.database import CelerySessionLocal
    from app.models import AppSettings

    async def _get():
        async with CelerySessionLocal() as sess:
            return await sess.get(AppSettings, 1)

    try:
        return asyncio.run(_get())
    except Exception:
        return None


# ── Core call with retry ──────────────────────────────────

@retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=15, max=120),
    retry=retry_if_exception_type(RateLimitError),
)
def _call(model_str: str, messages: list[dict], temperature: float, max_tokens: int, extra: dict) -> str:
    response = completion(
        model=model_str,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        **extra,
    )
    return response.choices[0].message.content or ""


def _dispatch(
    messages: list[dict],
    temperature: float,
    max_tokens: int,
) -> str:
    s = _load_settings()
    provider = (s.llm_provider if s else None) or "gemini"
    if provider not in PROVIDERS:
        logger.warning("llm.invalid_provider", provider=provider)
        provider = "gemini"
    model = (s.llm_model if s else None) or "gemini-2.5-flash"
    model_str = _model_string(provider, model)
    extra = _extra_kwargs(provider, s)

    logger.debug("llm.dispatch", provider=provider, model=model_str)

    try:
        return _call(model_str, messages, temperature, max_tokens, extra)
    except RateLimitError:
        raise
    except Exception as primary_exc:
        if provider != "nvidia" and _config.nvidia_api_key:
            fallback_str = _model_string("nvidia", "meta/llama-3.3-70b-instruct")
            fallback_extra = _extra_kwargs("nvidia", s)
            logger.warning(
                "llm.fallback_to_nvidia",
                primary_provider=provider,
                primary_model=model_str,
                fallback_model=fallback_str,
                reason=str(primary_exc)[:200],
            )
            return _call(fallback_str, messages, temperature, max_tokens, fallback_extra)
        raise


def call_llm(messages: list[dict], temperature: float = 0.2, max_tokens: int = 4096) -> str:
    """Call the configured model."""
    return _dispatch(messages=messages, temperature=temperature, max_tokens=max_tokens)


# Aliases kept for backward compat with existing task imports
call_pro = call_llm
call_flash = call_llm


# ── Model listing helpers ─────────────────────────────────

def list_models(provider: str, settings_row) -> list[str]:
    """Return available model IDs for a given provider. Keys from env vars. Best-effort; empty list on failure."""
    import httpx

    try:
        if provider == "openrouter":
            r = httpx.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {_config.openrouter_api_key}"},
                timeout=10,
            )
            return [m["id"] for m in r.json().get("data", [])]

        elif provider == "ollama":
            base = (settings_row.ollama_base_url if settings_row else None) or "http://localhost:11434"
            r = httpx.get(f"{base}/api/tags", timeout=8)
            return [m["name"] for m in r.json().get("models", [])]

        elif provider == "nvidia":
            base = (settings_row.nvidia_base_url if settings_row else None) or "https://integrate.api.nvidia.com/v1"
            r = httpx.get(
                f"{base}/models",
                headers={"Authorization": f"Bearer {_config.nvidia_api_key}"},
                timeout=10,
            )
            return [m["id"] for m in r.json().get("data", [])]

        elif provider == "anthropic":
            return [
                "claude-opus-4-7",
                "claude-sonnet-4-6",
                "claude-haiku-4-5-20251001",
                "claude-3-5-sonnet-20241022",
                "claude-3-5-haiku-20241022",
                "claude-3-opus-20240229",
            ]

        elif provider == "openai":
            r = httpx.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {_config.openai_api_key}"},
                timeout=10,
            )
            ids = [m["id"] for m in r.json().get("data", [])]
            return sorted(m for m in ids if "gpt" in m)

        elif provider in ("vertex", "gemini"):
            return [
                "gemini-2.5-pro",
                "gemini-2.5-flash",
                "gemini-2.0-flash",
                "gemini-2.0-flash-lite",
                "gemini-1.5-pro",
                "gemini-1.5-flash",
            ]

    except Exception as exc:
        logger.warning("list_models.failed", provider=provider, exc=str(exc))

    return []


def test_connection(provider: str, model: str, settings_row) -> dict:
    """Send a minimal ping to verify credentials and model. Keys from env vars. Returns {ok, error}."""
    model_str = _model_string(provider, model)
    extra = _extra_kwargs(provider, settings_row)
    try:
        _call(
            model_str=model_str,
            messages=[{"role": "user", "content": "Reply with the single word: pong"}],
            temperature=0,
            max_tokens=8,
            extra=extra,
        )
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
