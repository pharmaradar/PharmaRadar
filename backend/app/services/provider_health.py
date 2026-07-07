"""Provider health & usage tracking for the Settings page.

Live-checks every important API key/service we can reach cheaply:
- LLM keys (Gemini, OpenAI, Anthropic, OpenRouter, NVIDIA, Vertex) via a
  lightweight authenticated GET — surfaces invalid keys, rate limits, and
  (OpenRouter / NVIDIA) credit exhaustion.
- Voyage embeddings — key-configured check (no cheap balance endpoint).
- Apify: live monthly usage vs limit via the Apify REST API.
- TinyFish: no public balance API — we surface an exhaustion warning that is
  raised whenever a TinyFish CLI call fails with an "insufficient credits"
  error (set via flag_exhausted) and auto-clears on the next successful call.
- Infra: Postgres + Redis reachability, Vercel Blob configured.

The whole bundle is cached in Redis ~5 min; pass refresh=True to bust it.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)

_EXHAUST_PREFIX = "provider_exhausted:"
_TF_CREDIT_PREFIX = "tf_credits:"
_BUNDLE_CACHE_KEY = "provider_health:bundle:v3"
_BUNDLE_CACHE_TTL = 300  # 5 min


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _tf_suffix(key: str) -> str:
    """Stable per-key id (last 12 chars) — matches the rate limiter convention."""
    return key[-12:] if key else "default"


def _redis():
    try:
        import redis as _redis_mod
        return _redis_mod.Redis.from_url(get_settings().redis_url, socket_timeout=2)
    except Exception:
        return None


def _base(pid: str, name: str, configured: bool) -> dict:
    return {
        "id": pid, "name": name, "configured": configured,
        "status": "unknown", "usage_usd": None, "limit_usd": None,
        "percent": None, "usage_label": None, "message": "", "checked_at": _now(),
    }


# ── TinyFish per-key credit metering (we count calls; no balance API) ─────

def record_tinyfish_usage(key: str, n: int = 1) -> None:
    """Count a consumed TinyFish credit for this key in the current month."""
    r = _redis()
    if not r or not key:
        return
    try:
        rk = f"{_TF_CREDIT_PREFIX}{_month()}:{_tf_suffix(key)}"
        pipe = r.pipeline()
        pipe.incrby(rk, n)
        pipe.expire(rk, 35 * 24 * 3600)
        pipe.execute()
    except Exception:
        pass


def _tf_used(suffix: str) -> int:
    r = _redis()
    if not r:
        return 0
    try:
        v = r.get(f"{_TF_CREDIT_PREFIX}{_month()}:{suffix}")
        return int(v) if v else 0
    except Exception:
        return 0


# ── Exhaustion flags (written from failure paths, e.g. scraper) ──────────

def flag_exhausted(provider: str, message: str = "") -> None:
    r = _redis()
    if not r:
        return
    try:
        r.set(
            _EXHAUST_PREFIX + provider,
            json.dumps({"message": message, "at": _now()}),
            ex=7 * 24 * 3600,
        )
    except Exception:
        pass


def clear_exhausted(provider: str) -> None:
    r = _redis()
    if not r:
        return
    try:
        r.delete(_EXHAUST_PREFIX + provider)
    except Exception:
        pass


def _get_flag(provider: str) -> dict | None:
    r = _redis()
    if not r:
        return None
    try:
        v = r.get(_EXHAUST_PREFIX + provider)
        return json.loads(v) if v else None
    except Exception:
        return None


# ── Apify (live usage) ───────────────────────────────────────────────────

async def _apify_health(client) -> dict:
    s = get_settings()
    out = _base("apify", "Apify", bool(s.apify_api_token))
    if not s.apify_api_token:
        out["message"] = "APIFY_API_TOKEN not set"
        return out
    flag = _get_flag("apify")
    try:
        resp = await client.get(
            "https://api.apify.com/v2/users/me/limits",
            headers={"Authorization": f"Bearer {s.apify_api_token}"},
        )
        if resp.status_code == 200:
            d = resp.json().get("data", {}) or {}
            current = d.get("current", {}) or {}
            limits = d.get("limits", {}) or {}
            usage = current.get("monthlyUsageUsd")
            limit = limits.get("maxMonthlyUsageUsd")
            out["usage_usd"] = round(usage, 2) if isinstance(usage, (int, float)) else None
            out["limit_usd"] = round(limit, 2) if isinstance(limit, (int, float)) else None
            if out["usage_usd"] is not None and out["limit_usd"]:
                pct = round(out["usage_usd"] / out["limit_usd"] * 100, 1)
                out["percent"] = pct
                out["status"] = "exhausted" if pct >= 100 else "low" if pct >= 80 else "ok"
                out["message"] = f"${out['usage_usd']:.2f} of ${out['limit_usd']:.2f} this month"
            else:
                out["status"] = "ok"
                out["message"] = (f"${out['usage_usd']:.2f} used this month"
                                  if out["usage_usd"] is not None else "Connected")
        elif resp.status_code in (401, 403):
            out["status"] = "error"
            out["message"] = "Token invalid or unauthorized"
        else:
            out["status"] = "error"
            out["message"] = f"Apify API returned {resp.status_code}"
    except Exception as exc:
        out["status"] = "error"
        out["message"] = str(exc)[:160]
    if flag:
        out["status"] = "exhausted"
        out["message"] = flag.get("message") or "Apify reported insufficient credits"
    return out


# ── LLM key checks ─────────────────────────────────────────────────────────

def _openrouter_usage(resp, out) -> None:
    try:
        d = resp.json().get("data", {}) or {}
        usage = d.get("usage")
        limit = d.get("limit")
        if isinstance(usage, (int, float)):
            out["usage_usd"] = round(usage, 2)
        if isinstance(limit, (int, float)) and limit:
            out["limit_usd"] = round(limit, 2)
            pct = round((usage or 0) / limit * 100, 1)
            out["percent"] = pct
            out["status"] = "exhausted" if pct >= 100 else "low" if pct >= 80 else "ok"
            out["message"] = f"${(usage or 0):.2f} of ${limit:.2f} used"
        elif isinstance(usage, (int, float)):
            out["message"] = f"${usage:.2f} used (no limit)"
    except Exception:
        pass


async def _check_llm(client, pid, name, key, url, headers, on_ok=None) -> dict:
    out = _base(pid, name, bool(key))
    if not key:
        out["message"] = "Not configured"
        return out
    try:
        resp = await client.get(url, headers=headers)
        sc = resp.status_code
        if sc == 200:
            out["status"] = "ok"
            out["message"] = "Key valid"
            if on_ok:
                on_ok(resp, out)
        elif sc == 429:
            out["status"] = "low"
            out["message"] = "Rate limited (429)"
        elif sc == 403 and pid == "nvidia":
            out["status"] = "exhausted"
            out["message"] = "Free credits likely exhausted (403)"
        elif sc in (401, 403):
            out["status"] = "error"
            out["message"] = f"Auth failed ({sc}) — check key"
        else:
            out["status"] = "error"
            out["message"] = f"HTTP {sc}"
    except Exception as exc:
        out["status"] = "error"
        out["message"] = str(exc)[:160]
    return out


async def _llm_checks(client) -> list[dict]:
    s = get_settings()
    tasks = []
    if s.gemini_api_key:
        tasks.append(_check_llm(
            client, "gemini", "Gemini", s.gemini_api_key,
            f"https://generativelanguage.googleapis.com/v1beta/models?key={s.gemini_api_key}",
            {},
        ))
    if s.openai_api_key:
        tasks.append(_check_llm(
            client, "openai", "OpenAI", s.openai_api_key,
            "https://api.openai.com/v1/models",
            {"Authorization": f"Bearer {s.openai_api_key}"},
        ))
    if s.anthropic_api_key:
        tasks.append(_check_llm(
            client, "anthropic", "Anthropic", s.anthropic_api_key,
            "https://api.anthropic.com/v1/models",
            {"x-api-key": s.anthropic_api_key, "anthropic-version": "2023-06-01"},
        ))
    if s.openrouter_api_key:
        tasks.append(_check_llm(
            client, "openrouter", "OpenRouter", s.openrouter_api_key,
            "https://openrouter.ai/api/v1/key",
            {"Authorization": f"Bearer {s.openrouter_api_key}"},
            on_ok=_openrouter_usage,
        ))
    if s.nvidia_api_key:
        tasks.append(_check_llm(
            client, "nvidia", "NVIDIA NIM", s.nvidia_api_key,
            "https://integrate.api.nvidia.com/v1/models",
            {"Authorization": f"Bearer {s.nvidia_api_key}"},
        ))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out = [r for r in results if isinstance(r, dict)]

    # Vertex + Voyage: no cheap live check — configured-only
    if s.google_cloud_project or s.google_application_credentials:
        v = _base("vertex", "Vertex AI", True)
        v["status"] = "ok"
        v["message"] = f"Project {s.google_cloud_project or 'set'} (service account)"
        out.append(v)
    if s.voyage_api_key:
        v = _base("voyage", "Voyage (embeddings)", True)
        v["status"] = "ok"
        v["message"] = "Key configured"
        out.append(v)
    return out


# ── TinyFish (flag-based) ────────────────────────────────────────────────

def _tinyfish_rows() -> list[dict]:
    """One row per TinyFish key — estimated credits used this month (no balance API)."""
    s = get_settings()
    keys = s.tinyfish_keys_list
    if not keys:
        out = _base("tinyfish", "TinyFish", False)
        out["message"] = "No TinyFish API key configured"
        return [out]

    cap = s.tinyfish_monthly_credits or 500
    rows = []
    for i, key in enumerate(keys, 1):
        suffix = _tf_suffix(key)
        used = _tf_used(suffix)
        remaining = max(0, cap - used)
        out = _base(f"tinyfish:{suffix}", f"TinyFish · key {i} (…{key[-4:]})", True)
        pct = round(used / cap * 100, 1) if cap else None
        out["percent"] = pct
        out["usage_label"] = f"{used} / {cap} credits · {remaining} left"
        flag = _get_flag(f"tinyfish:{suffix}")
        if flag:
            out["status"] = "exhausted"
            out["message"] = (flag.get("message") or "Reported insufficient credits") + \
                " — top up at agent.tinyfish.ai/credits"
        elif pct is not None and pct >= 100:
            out["status"] = "exhausted"
            out["message"] = "Monthly credits used up (estimated from calls)"
        elif pct is not None and pct >= 80:
            out["status"] = "low"
            out["message"] = "Approaching monthly limit (estimated from calls)"
        else:
            out["status"] = "ok"
            out["message"] = "Estimated from successful calls — resets monthly"
        rows.append(out)
    return rows


# ── Infra ───────────────────────────────────────────────────────────────

def _redis_health() -> dict:
    out = _base("redis", "Redis", True)
    r = _redis()
    try:
        if r and r.ping():
            out["status"] = "ok"
            out["message"] = "Connected"
        else:
            out["status"] = "error"
            out["message"] = "No connection"
    except Exception as exc:
        out["status"] = "error"
        out["message"] = str(exc)[:120]
    return out


async def _db_health() -> dict:
    out = _base("database", "Postgres", True)
    try:
        from sqlalchemy import text
        from app.database import engine
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        out["status"] = "ok"
        out["message"] = "Connected"
    except Exception as exc:
        out["status"] = "error"
        out["message"] = str(exc)[:140]
    return out


def _blob_health() -> dict:
    s = get_settings()
    out = _base("blob", "Vercel Blob", bool(s.vercel_blob_token))
    if s.vercel_blob_token:
        out["status"] = "ok"
        out["message"] = "Token configured (public PDF store)"
    else:
        out["message"] = "BLOB token not set"
    return out


# ── Public API ────────────────────────────────────────────────────────────

async def get_provider_health(refresh: bool = False) -> dict:
    r = _redis()
    if not refresh and r:
        try:
            cached = r.get(_BUNDLE_CACHE_KEY)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            llm, apify = await asyncio.gather(_llm_checks(client), _apify_health(client))
    except Exception as exc:
        logger.warning("provider_health.http_failed", exc=str(exc)[:160])
        llm, apify = [], _base("apify", "Apify", bool(get_settings().apify_api_token))

    db = await _db_health()

    providers = [*llm, apify, *_tinyfish_rows(), db, _redis_health(), _blob_health()]
    bundle = {"providers": providers, "checked_at": _now()}

    if r:
        try:
            r.set(_BUNDLE_CACHE_KEY, json.dumps(bundle), ex=_BUNDLE_CACHE_TTL)
        except Exception:
            pass
    return bundle
