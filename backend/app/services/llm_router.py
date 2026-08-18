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
import threading
from concurrent.futures import ThreadPoolExecutor

from litellm import completion, RateLimitError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

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
    """Load AppSettings synchronously — safe from BOTH calling contexts.

    - Sync Celery worker (no event loop in this thread): plain asyncio.run.
    - Async FastAPI handler thread: asyncio.run() raises "cannot be called from
      a running event loop", which used to be swallowed (with a "coroutine was
      never awaited" RuntimeWarning) and returned None — silently ignoring the
      DB-configured provider/model for every backend LLM endpoint, i.e. the
      Settings-page provider switch was a no-op there. Run the loader in a
      throwaway thread in that case.
    """
    import asyncio
    from app.database import CelerySessionLocal
    from app.models import AppSettings

    async def _get():
        async with CelerySessionLocal() as sess:
            return await sess.get(AppSettings, 1)

    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_get())   # normal path: worker thread, no loop
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _get()).result(timeout=10)
    except Exception:
        return None


# ── Core call with retry ──────────────────────────────────

# Providers return HTTP 429 for two completely different situations, and litellm
# maps both to RateLimitError:
#
#   transient  "too many requests per minute"     → backing off WORKS
#   permanent  "prepayment credits are depleted"  → backing off can never work
#
# Treating the second as the first is expensive in three ways at once: every
# call burns the full 15+30+60s backoff ladder before failing, the fallback
# provider is skipped (see _dispatch), and tasks that loop over LLM calls blow
# their Celery time limits and get the worker SIGKILLed. That is what happened on
# 2026-08-17 — Gemini credits ran out and classify_ae_backfill overran its 720s
# hard limit, because each of its calls sat in a doomed retry ladder.
#
# Matched on the message because that is the only place the distinction exists:
# the status code, the exception type and the litellm mapping are identical for
# both. Kept deliberately narrow — anything unrecognised stays retryable, since
# retrying a transient error is cheap and refusing to retry one is not.
_QUOTA_EXHAUSTED_MARKERS = (
    "prepayment credits are depleted",
    "credits are depleted",
    "resource_exhausted",
    "insufficient credits",
    "insufficient_quota",
    "billing",
    "exceeded your current quota",
)


def is_quota_exhausted(exc: BaseException) -> bool:
    """True when a 429 means 'out of money', not 'slow down'."""
    text = str(exc).lower()
    return any(m in text for m in _QUOTA_EXHAUSTED_MARKERS)


def _retryable(exc: BaseException) -> bool:
    return isinstance(exc, RateLimitError) and not is_quota_exhausted(exc)


# provider_health owns the Redis flag the Settings page reads. Imported lazily
# and never allowed to raise: a monitoring breadcrumb must not be able to fail
# an LLM call that otherwise worked.
def _flag_exhausted(provider: str, message: str) -> None:
    try:
        from app.services.provider_health import flag_exhausted
        flag_exhausted(provider, message[:300])
    except Exception:
        pass


def _clear_exhausted(provider: str) -> None:
    try:
        from app.services.provider_health import clear_exhausted
        clear_exhausted(provider)
    except Exception:
        pass


# litellm's default request_timeout is 6000s — 100 minutes, longer than every
# Celery time limit in this app by an order of magnitude. A provider that accepts
# the connection and then never answers therefore parks the worker until the hard
# limit SIGKILLs it, which reads in the logs as "the task is slow" rather than
# "the provider stalled". Measured 2026-08-17 against NVIDIA NIM: the connection
# opened in 8ms and returned zero bytes for 120s, on 3 of 4 attempts.
#
# 120s is well above a real generation (8192 tokens of Gemini Flash lands in
# 10-30s) and well under the 300s floor of our shortest task budget. A timeout
# raises litellm.Timeout, not RateLimitError, so it is NOT caught by the retry
# predicate below — it falls straight through to the fallback provider in
# _dispatch, which is bounded by this same timeout. Worst case per call is
# therefore 2 x 120s, not 2 x 6000s.
_LLM_TIMEOUT_SECONDS = 120


@retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=15, max=120),
    # retry_if_exception passes the EXCEPTION to the predicate. A bare callable
    # here would receive tenacity's RetryCallState instead, the isinstance check
    # would be False for every error, and retries would silently never happen.
    retry=retry_if_exception(_retryable),
)
def _call(model_str: str, messages: list[dict], temperature: float, max_tokens: int,
          extra: dict, provider: str | None = None) -> str:
    response = completion(
        model=model_str,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=_LLM_TIMEOUT_SECONDS,
        **extra,
    )
    _meter(provider, model_str, response)
    return response.choices[0].message.content or ""


def _meter(provider: str | None, model_str: str, response) -> None:
    """Record what this call cost, for the Settings spend panel.

    Metered here rather than at the call sites because this is the one place
    every provider's traffic passes through, and the only place the real token
    counts exist — estimating from len(prompt) would be fiction. Wrapped so a
    telemetry failure can never fail an LLM call that actually succeeded.

    `provider` is the app's own id ("gemini", "nvidia", ...) and must be passed
    explicitly: it cannot be recovered from model_str, because _model_string
    renames some providers (nvidia -> "nvidia_nim/", vertex -> "vertex_ai/") and
    gives OpenAI and Anthropic no prefix at all. Deriving it would file OpenAI
    spend under "gpt-4o" and never match the health panel's rows.
    """
    try:
        usage = getattr(response, "usage", None)
        if usage is None or not provider:
            return
        from app.services.provider_health import record_llm_usage
        record_llm_usage(
            provider=provider,
            model=model_str,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )
    except Exception:
        pass


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
        result = _call(model_str, messages, temperature, max_tokens, extra, provider)
    except RateLimitError as exc:
        # A transient rate limit is the one case where the fallback is the wrong
        # move: _call already backed off against this provider, and switching
        # models mid-run changes the voice of the output for no reason. Credit
        # exhaustion is the opposite — this provider is done until someone tops
        # it up, so fall through to the fallback rather than failing the request.
        if not is_quota_exhausted(exc):
            raise
        _flag_exhausted(provider, str(exc))
        logger.warning("llm.quota_exhausted", provider=provider, model=model_str)
        if provider == "nvidia" or not _config.nvidia_api_key:
            raise
        fallback_str = _model_string("nvidia", "meta/llama-3.3-70b-instruct")
        return _call(fallback_str, messages, temperature, max_tokens,
                     _extra_kwargs("nvidia", s), "nvidia")
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
            return _call(fallback_str, messages, temperature, max_tokens,
                         fallback_extra, "nvidia")
        raise

    # Reaching here means the configured provider answered, so any stale
    # exhaustion warning on the Settings page is now wrong. Same auto-clear
    # convention TinyFish uses.
    _clear_exhausted(provider)
    return result


def call_llm(messages: list[dict], temperature: float = 0.2, max_tokens: int = 4096) -> str:
    """Call the configured model."""
    return _dispatch(messages=messages, temperature=temperature, max_tokens=max_tokens)


# LLM calls get their OWN thread pool, deliberately not the default one.
#
# `run_in_executor(None, …)` shares a single pool sized `min(32, cpu+4)` — 12
# slots on an 8-core box, fewer on a small Railway container. Eight subsystems
# share it: LLM calls, Apify fetches, TinyFish scraping, the literature APIs,
# the agent. An LLM round-trip blocks a slot for 10-30s, so a handful of
# simultaneous "Regenerate" clicks can occupy every slot and stall scraping and
# account collection behind them — work that has nothing to do with the clicks.
#
# A separate bounded pool means heavy LLM use degrades LLM latency only.
_LLM_POOL_SIZE = 6
_llm_pool: "ThreadPoolExecutor | None" = None
_llm_pool_lock = threading.Lock()


def _get_llm_pool():
    global _llm_pool
    if _llm_pool is None:
        with _llm_pool_lock:
            if _llm_pool is None:
                _llm_pool = ThreadPoolExecutor(
                    max_workers=_LLM_POOL_SIZE, thread_name_prefix="llm")
    return _llm_pool


async def call_llm_async(messages: list[dict], temperature: float = 0.2,
                         max_tokens: int = 4096) -> str:
    """Awaitable wrapper for async FastAPI handlers.

    call_llm blocks for the full LLM round-trip (~10s on Gemini) — calling it
    directly inside an `async def` route freezes the whole event loop and every
    concurrent request with it. This runs it in a dedicated pool so it cannot
    starve scraping and collection of their threads.
    """
    import asyncio
    from functools import partial
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _get_llm_pool(),
        partial(call_llm, messages, temperature=temperature, max_tokens=max_tokens))


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
            max_tokens=64,
            extra=extra,
        )
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── In-flight de-duplication ──────────────────────────────

# Two clicks on the same Regenerate produce two identical LLM runs. Measured:
# firing kol-brief twice concurrently generated it twice — same prompt, same
# corpus, double the cost, and the loser's result is thrown away by
# last-write-wins. This lets the second caller await the first instead.
#
# In-process only, which matches how these endpoints are used (one API process
# per Railway service). A multi-replica deploy would need the flag in Redis;
# the failure mode there is the current behaviour, not something worse.
_inflight: dict[str, "asyncio.Future"] = {}
_inflight_lock = threading.Lock()


async def once_only(key: str, factory):
    """Run `factory()` once for a given key; concurrent callers share the result.

    `factory` is an async callable. Exceptions propagate to every waiter, and
    the key is always released so a failure never wedges later attempts.
    """
    import asyncio

    loop = asyncio.get_event_loop()
    with _inflight_lock:
        running = _inflight.get(key)
        if running is not None and not running.done():
            follow = running          # someone else is already doing this work
        else:
            follow = None
            running = loop.create_future()
            _inflight[key] = running

    if follow is not None:
        return await follow

    try:
        result = await factory()
        if not running.done():
            running.set_result(result)
        return result
    except Exception as exc:                        # noqa: BLE001 - shared with waiters
        if not running.done():
            running.set_exception(exc)
        raise
    finally:
        with _inflight_lock:
            if _inflight.get(key) is running:
                _inflight.pop(key, None)
