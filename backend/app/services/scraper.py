"""TinyFish scraping service — parallel 3-pass pipeline.

Parallelism model
─────────────────
• Search queries fire in parallel    (ThreadPoolExecutor, max 5)
• URL fetches fire in parallel       (ThreadPoolExecutor, max 5 per target)
• Rate limiting is global via Redis  (sliding window per API key, shared across all workers)
• DB saves are thread-safe           (each thread gets its own asyncio event loop — no nesting)

3-pass logic
────────────
Pass 1  Search (10 rich queries, parallel) → parallel fetch/agent per URL
Pass 2  Agent rescue on known_urls if Pass 1 yields 0 posts
Pass 3  Re-extract from stored posts if still nothing (extended window)
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from urllib.parse import urlparse

import structlog

from app.config import get_settings
from app.services.deduplicator import sha256_hash
from app.services.run_context import RunContext

logger = structlog.get_logger(__name__)
settings = get_settings()


def _run_in_thread(coro):
    """Run an async coroutine from a sync context with its own dedicated event loop.

    Replaces asyncio.run() at call sites that may execute inside a thread which
    has already run an async block (e.g. ThreadPoolExecutor threads, or Celery
    tasks that chain into other async calls). asyncio.run() refuses to start
    when any loop is currently running in the thread; this helper creates a
    fresh loop, runs the coroutine to completion, then tears the loop down so
    no state leaks between calls.
    """
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.close()
        finally:
            asyncio.set_event_loop(None)


_FRESHNESS_DAYS   = 90
_EXTENDED_DAYS    = 365
_FETCH_WORKERS    = 5   # parallel URL fetches per target
_SEARCH_WORKERS   = 5   # parallel search queries per target

# Set to True when any TinyFish account reports "Insufficient credits" for agent calls.
# Short-circuits wave 2 so we don't burn 120s timeouts on every URL pointlessly.
_AGENT_CREDITS_EXHAUSTED: bool = False
_AGENT_CREDITS_LOCK = threading.Lock()

ROCHE_DRUGS = [
    "Tecentriq", "Ocrevus", "Hemlibra", "Kadcyla", "Perjeta",
    "Avastin", "Herceptin", "Xolair", "Polivy", "Lunsumio",
]

NEWS_SITES = [
    # Pharma / biotech news
    "statnews.com", "endpoints.news", "fiercepharma.com", "biopharmadive.com",
    "pharmatimes.com", "pharmaphorum.com", "drugdiscoverytoday.com",
    "healio.com", "mdedge.com", "cancernetwork.com", "onclive.com",
    # General news
    "reuters.com", "bloomberg.com", "forbes.com", "wsj.com",
    "ft.com", "theguardian.com", "bbc.com",
    # Medical journals (open)
    "nature.com", "nejm.org", "medscape.com", "medicalnewstoday.com",
    "nih.gov", "ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov",
    # Conferences
    "asco.org", "esmo.org", "aacr.org", "aacrjournals.org",
]

LIKELY_NEEDS_AGENT = {
    # Social media — require JS / login
    "twitter.com", "x.com", "linkedin.com", "instagram.com", "facebook.com",
    "threads.net", "bluesky.app", "bsky.app", "mastodon.social",
    "reddit.com",
    # Paywalled journals
    "aacrjournals.org", "sciencedirect.com", "wiley.com", "onlinelibrary.wiley.com",
    "springer.com", "link.springer.com", "jamanetwork.com", "thelancet.com",
    "cell.com", "bmj.com", "academic.oup.com", "ascopubs.org", "annalsofoncology.org",
    "nejm.org", "nature.com", "jto.org", "ssrn.com", "ovid.com",
    "karger.com", "tandfonline.com", "sagepub.com", "mdpi.com",
    "researchgate.net", "europepmc.org", "frontiersin.org",
    "jnccn.org", "annalsofoncology.org", "thoraciconcology.org",
    # Other JS-heavy sites
    "clinicaltrials.gov", "who.int", "cancer.gov",
}

HIGH_SIGNAL_DOMAINS = {
    # Social (KOL posts)
    "twitter.com", "x.com", "linkedin.com", "substack.com", "threads.net",
    # Top pharma/biotech news
    "statnews.com", "endpoints.news", "fiercepharma.com", "biopharmadive.com",
    "pharmaphorum.com", "healio.com", "oncologynurseadvisor.com",
    "cancernetwork.com", "onclive.com", "targetedonc.com",
    # Top journals
    "reuters.com", "bloomberg.com", "nature.com", "nejm.org", "thelancet.com",
    "jamanetwork.com", "cell.com", "asco.org", "esmo.org", "aacr.org",
    "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov",
}

HARD_SKIP_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
    ".mp4", ".mov", ".avi", ".mp3", ".wav",
    ".zip", ".tar.gz", ".docx", ".doc", ".xlsx", ".pptx",
)

AGENT_FETCH_GOAL = (
    "Extract the full main content of this page as plain text — the article body, "
    "post content, interview transcript, or commentary. Include any direct quotes "
    "from the author. Skip navigation, ads, cookie banners, and footers. "
    "Return only the readable body text."
)


# ── API key rotation ──────────────────────────────────────
_key_lock = threading.Lock()
_key_cycle: itertools.cycle | None = None
_pipeline_key_cycle: itertools.cycle | None = None  # all except last

PIPELINE_RUNNING_REDIS_KEY = "pipeline:running"
DISCOVERY_ACTIVE_REDIS_KEY = "discovery:active"


def _redis_flag(flag_key: str) -> bool:
    """Check a boolean Redis flag. Returns False on error."""
    try:
        import redis as _redis
        r = _redis.Redis.from_url(settings.redis_url, socket_timeout=1)
        return bool(r.get(flag_key))
    except Exception:
        return False


def _next_key(pipeline_mode: bool = False) -> str:
    """Pick the next TinyFish API key.

    Smart allocation when multiple keys exist:
    - Discovery active + pipeline running → pipeline gets all except last key
    - Otherwise → all keys available
    """
    global _key_cycle, _pipeline_key_cycle
    keys = settings.tinyfish_keys_list
    if not keys:
        return ""

    # Only apply smart allocation when we have >1 key and both sides are active
    if (pipeline_mode and len(keys) > 1
            and _redis_flag(PIPELINE_RUNNING_REDIS_KEY)
            and _redis_flag(DISCOVERY_ACTIVE_REDIS_KEY)):
        pipeline_keys = keys[:-1]
        with _key_lock:
            if _pipeline_key_cycle is None:
                _pipeline_key_cycle = itertools.cycle(pipeline_keys)
            return next(_pipeline_key_cycle)

    with _key_lock:
        if _key_cycle is None:
            _key_cycle = itertools.cycle(keys)
        return next(_key_cycle)


def _discovery_key() -> str:
    """Pick TinyFish key for Discovery.

    If pipeline is running and we have >1 key → use last key exclusively.
    Otherwise → use all keys via normal round-robin.
    """
    keys = settings.tinyfish_keys_list
    if not keys:
        return ""
    if len(keys) > 1 and _redis_flag(PIPELINE_RUNNING_REDIS_KEY):
        return keys[-1]
    with _key_lock:
        global _key_cycle
        if _key_cycle is None:
            _key_cycle = itertools.cycle(keys)
        return next(_key_cycle)


def _tf_env(key: str = "", pipeline_mode: bool = False) -> tuple:
    env = os.environ.copy()
    k = key or _next_key(pipeline_mode=pipeline_mode)
    if k:
        env["TINYFISH_API_KEY"] = k
    return env, k


# ── Redis rate limiter — shared across ALL workers ────────
# Hard ceiling: a single rate-limit wait can never exceed this many seconds.
# Without this, a misconfigured limit or a stuck Redis key could spin a worker
# slot forever and re-create the "all 4 slots wedged" deadlock.
_RATE_LIMIT_MAX_WAIT = 30


def _rate_limit_wait(key: str) -> None:
    """Sliding-window rate limiter per API key, enforced via Redis.
    Premium: set TINYFISH_RATE_LIMIT_PER_KEY=300 in .env.
    Blocks the calling thread until a slot is available, but never longer
    than _RATE_LIMIT_MAX_WAIT seconds — after that, give up and let the
    request through (better to risk one 429 than to wedge a worker)."""
    limit = settings.tinyfish_rate_limit_per_key
    if limit <= 0:
        return
    window = 60  # seconds
    redis_key = f"tf_rate:{key[-12:] if key else 'default'}"
    deadline = time.time() + _RATE_LIMIT_MAX_WAIT
    try:
        import redis as _redis
        r = _redis.Redis.from_url(settings.redis_url, socket_timeout=2)
        while True:
            now = time.time()
            if now >= deadline:
                logger.warning("scrape.rate_limit_wait_exceeded",
                               key_suffix=key[-12:] if key else "default",
                               max_wait=_RATE_LIMIT_MAX_WAIT)
                return
            pipe = r.pipeline(True)
            pipe.zremrangebyscore(redis_key, "-inf", now - window)
            pipe.zcard(redis_key)
            pipe.zadd(redis_key, {f"{now:.6f}": now})
            pipe.expire(redis_key, window + 5)
            _, count, _, _ = pipe.execute()
            if count < limit:
                return
            # Window full — sleep until the oldest entry expires (capped)
            oldest = r.zrange(redis_key, 0, 0, withscores=True)
            wait = max(0.1, (oldest[0][1] + window - now) if oldest else 1.0)
            remaining = deadline - now
            time.sleep(min(wait, 2.0, max(0.05, remaining)))
    except Exception:
        # Redis unavailable — conservative fallback sleep, also bounded
        time.sleep(min(_RATE_LIMIT_MAX_WAIT, 60.0 / max(1, limit)))


# ── Low-level TinyFish calls ──────────────────────────────

def _run_tf(args: list[str], timeout: int = 120, pipeline_mode: bool = True) -> tuple[dict, str]:
    """Run tinyfish CLI, return (parsed_json, key_used)."""
    env, key = _tf_env(pipeline_mode=pipeline_mode)
    _rate_limit_wait(key)           # ← blocks here if rate limited
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
        if r.returncode != 0:
            stderr_snippet = (r.stderr or "")[:300]
            logger.debug("tinyfish.nonzero", cmd=args[1] if len(args) > 1 else "",
                         returncode=r.returncode, stderr=stderr_snippet[:200])
            if "insufficient credits" in stderr_snippet.lower() or "0 credits remaining" in stderr_snippet.lower():
                global _AGENT_CREDITS_EXHAUSTED
                with _AGENT_CREDITS_LOCK:
                    if not _AGENT_CREDITS_EXHAUSTED:
                        logger.warning("tinyfish.agent_credits_exhausted", key=key)
                        _AGENT_CREDITS_EXHAUSTED = True
                try:
                    from app.services.provider_health import flag_exhausted
                    flag_exhausted(f"tinyfish:{key[-12:]}", stderr_snippet[:200])
                except Exception:
                    pass
            return {}, key
        out = r.stdout.strip()
        try:
            parsed = json.loads(out) if out else {}
        except json.JSONDecodeError:
            return {}, key
        # Successful CLI call — count the credit and clear any stale flag
        try:
            from app.services.provider_health import record_tinyfish_usage, clear_exhausted
            record_tinyfish_usage(key)
            clear_exhausted(f"tinyfish:{key[-12:]}")
        except Exception:
            pass
        return parsed, key
    except FileNotFoundError:
        logger.error("tinyfish.not_installed")
        return {}, key
    except subprocess.TimeoutExpired:
        logger.debug("tinyfish.timeout", cmd=args[1] if len(args) > 1 else "")
        return {}, key
    except Exception as exc:
        logger.debug("tinyfish.error", exc=str(exc)[:200])
        return {}, key


def _tf_search(query: str) -> list[dict]:
    data, _ = _run_tf(["tinyfish", "search", "query", query], pipeline_mode=True)
    return data.get("results", [])


def _tf_fetch(url: str) -> str:
    data, _ = _run_tf(["tinyfish", "fetch", "content", "get", url], pipeline_mode=True)
    results = data.get("results", [])
    if results:
        return results[0].get("text") or results[0].get("content") or ""
    return ""


def _tf_search_discovery(query: str) -> list[dict]:
    """Discovery-aware search — uses dedicated key when pipeline is running."""
    key = _discovery_key()
    env = os.environ.copy()
    if key:
        env["TINYFISH_API_KEY"] = key
    _rate_limit_wait(key)
    import subprocess as _sp, json as _json
    try:
        r = _sp.run(["tinyfish", "search", "query", query],
                    capture_output=True, text=True, timeout=120, env=env)
        out = r.stdout.strip()
        data = _json.loads(out) if out else {}
        if r.returncode == 0:
            try:
                from app.services.provider_health import record_tinyfish_usage
                record_tinyfish_usage(key)
            except Exception:
                pass
        return data.get("results", [])
    except Exception:
        return []


def _tf_fetch_discovery(url: str) -> str:
    """Discovery-aware fetch — uses dedicated key when pipeline is running."""
    key = _discovery_key()
    env = os.environ.copy()
    if key:
        env["TINYFISH_API_KEY"] = key
    _rate_limit_wait(key)
    import subprocess as _sp, json as _json
    try:
        r = _sp.run(["tinyfish", "fetch", "content", "get", url],
                    capture_output=True, text=True, timeout=120, env=env)
        out = r.stdout.strip()
        data = _json.loads(out) if out else {}
        if r.returncode == 0:
            try:
                from app.services.provider_health import record_tinyfish_usage
                record_tinyfish_usage(key)
            except Exception:
                pass
        results = data.get("results", [])
        if results:
            return results[0].get("text") or results[0].get("content") or ""
    except Exception:
        pass
    return ""


def _tf_agent(url: str) -> str:
    """Run TinyFish agent on a URL. Handles all response shapes the agent can return."""
    data, _ = _run_tf(
        ["tinyfish", "agent", "run", "--url", url, "--sync", AGENT_FETCH_GOAL],
        timeout=180,
    )
    if not isinstance(data, dict):
        return ""

    # Shape 1: flat text fields
    for k in ("content", "text", "body", "output", "answer"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v

    # Shape 2: {"results": [{"text": ...}]}
    results = data.get("results")
    if isinstance(results, list) and results:
        first = results[0]
        if isinstance(first, dict):
            for k in ("text", "content", "body"):
                v = first.get(k)
                if isinstance(v, str) and v.strip():
                    return v

    # Shape 3: {"status": "COMPLETED", "result": {...}} — agent returned structured JSON
    # Serialize the result dict as text so the LLM can still extract from it
    result = data.get("result")
    if result and data.get("status") == "COMPLETED":
        if isinstance(result, str) and result.strip():
            return result
        if isinstance(result, dict):
            # Convert structured JSON to readable text — LLM handles this fine
            return json.dumps(result, ensure_ascii=False, indent=2)

    return ""


def _fetch_failed(content: str) -> bool:
    if not content or not content.strip():
        return True
    low = content.lower()
    return "bot_blocked" in low or "target_http_error" in low


# ── Agent budget (Redis INCR, shared across workers) ──────

def _agent_can_consume(run_id: int) -> bool:
    try:
        import redis as _redis
        r = _redis.Redis.from_url(settings.redis_url, socket_timeout=2)
        key = f"run:{run_id}:agent_used"
        used = r.incr(key)
        r.expire(key, 86400)
        return used <= settings.agent_budget_per_run
    except Exception:
        return True


# ── URL helpers ───────────────────────────────────────────

def _domain(url: str) -> str:
    try:
        h = (urlparse(url).hostname or "").lower()
    except Exception:
        h = ""
    return h[4:] if h.startswith("www.") else h


def _is_binary(url: str) -> bool:
    return (url or "").lower().endswith(HARD_SKIP_SUFFIXES)


def _signal_score(url: str, ids: dict) -> int:
    host = _domain(url)
    score = 0
    for handle in ids.values():
        if handle and handle.lower() in url.lower():
            score = max(score, 10)
    if any(host == d or host.endswith("." + d) for d in HIGH_SIGNAL_DOMAINS):
        score = max(score, 8)
    if any(n in host for n in NEWS_SITES):
        score = max(score, 6)
    return score


# ── Query building ────────────────────────────────────────

def extract_identifiers(known_urls: list[str]) -> dict:
    ids: dict[str, str] = {}
    for url in (known_urls or []):
        url = url.rstrip("/")
        parts = url.split("/")
        if "twitter.com" in url or "x.com" in url:
            handle = parts[-1].lstrip("@")
            if handle:
                ids["twitter"] = handle
        elif "linkedin.com/in/" in url:
            ids["linkedin"] = parts[-1]
        elif "substack.com" in url:
            if "@" in parts[-1]:
                ids["substack"] = parts[-1].lstrip("@")
            elif ".substack.com" in url:
                ids["substack"] = url.split(".substack.com")[0].split("//")[-1]
        elif "bsky.app" in url or "bluesky" in url:
            ids["bluesky"] = parts[-1].lstrip("@")
        elif "threads.net" in url:
            ids["threads"] = parts[-1].lstrip("@")
        elif "researchgate.net/profile/" in url:
            ids["researchgate"] = parts[-1]
        elif "youtube.com" in url:
            # channel or @handle
            if "@" in url:
                handle = [p for p in parts if p.startswith("@")]
                if handle:
                    ids["youtube"] = handle[0]
    return ids


def build_search_queries(name: str, ids: dict, window_days: int = _FRESHNESS_DAYS) -> list[str]:
    cutoff_90  = (date.today() - timedelta(days=90)).isoformat()
    cutoff_1yr = (date.today() - timedelta(days=365)).isoformat()
    f90  = f"after:{cutoff_90}"
    f1yr = f"after:{cutoff_1yr}"
    drugs = " OR ".join(ROCHE_DRUGS[:6])
    news  = " OR ".join(f"site:{s}" for s in NEWS_SITES[:5])

    queries = [
        f'"{name}" Roche {f90}',
        f'"{name}" pharmaceutical OR oncology {f90}',
        f'"{name}" ({news}) {f90}',
        f'"{name}" {drugs} {f1yr}',
        f'"{name}" Roche FDA OR EMA OR clinical trial {f1yr}',
        f'"{name}" ESMO OR ASCO OR AACR {f1yr}',
        f'"{name}" drug approval OR immunotherapy OR biomarker {f1yr}',
        f'"{name}" cancer treatment OR lung cancer OR NSCLC {f1yr}',
        f'"{name}" interview OR conference OR publication {f1yr}',
        f'"{name}" pharma OR oncology site:researchgate.net OR site:pubmed.ncbi.nlm.nih.gov',
        # Social sites — always search regardless of known handles
        f'"{name}" site:linkedin.com {f1yr}',
        f'"{name}" site:twitter.com OR site:x.com {f90}',
        f'"{name}" site:threads.net OR site:bsky.app {f90}',
        f'"{name}" site:youtube.com {f1yr}',
        f'"{name}" site:reddit.com pharma OR oncology {f1yr}',
    ]

    twitter = ids.get("twitter")
    if twitter:
        queries += [
            f"site:twitter.com/{twitter} {f90}",
            f"site:x.com/{twitter} {f90}",
        ]
    substack = ids.get("substack")
    if substack:
        queries.append(f"site:{substack}.substack.com {f1yr}")
    linkedin = ids.get("linkedin")
    if linkedin:
        queries.append(f"site:linkedin.com/in/{linkedin} {f1yr}")
    bluesky = ids.get("bluesky")
    if bluesky:
        queries.append(f"site:bsky.app {bluesky} {f90}")
    threads = ids.get("threads")
    if threads:
        queries.append(f"site:threads.net/@{threads} {f90}")
    rg = ids.get("researchgate")
    if rg:
        queries.append(f"site:researchgate.net/profile/{rg} {f1yr}")
    yt = ids.get("youtube")
    if yt:
        queries.append(f"site:youtube.com {yt} pharma OR oncology {f1yr}")

    return queries


# ── Post persistence ──────────────────────────────────────

async def _save_post_and_extract(
    target_id: int, url: str, content: str,
    idempotency_key: str, run_id: int,
) -> tuple[bool, int | None]:
    from app.database import CelerySessionLocal
    from app.models import ScrapedPost

    h = sha256_hash(content)
    post = ScrapedPost(
        target_id=target_id, source_url=url, raw_content=content,
        content_hash=h, idempotency_key=f"{idempotency_key}:{h[:16]}",
    )
    async with CelerySessionLocal() as sess:
        try:
            sess.add(post)
            await sess.commit()
            await sess.refresh(post)
        except Exception:
            await sess.rollback()
            return False, None

    return True, post.id


def _save_post_sync(target_id, url, content, idempotency_key, run_id) -> tuple[bool, int | None]:
    """Thread-safe wrapper — runs _save_post_and_extract in a fresh event loop.
    Safe to call from ThreadPoolExecutor threads (no event loop nesting)."""
    return _run_in_thread(_save_post_and_extract(target_id, url, content, idempotency_key, run_id))


# ── Per-URL worker — FREE FETCH ONLY (no agent in Pass 1) ────────────────

def _needs_agent(url: str) -> bool:
    """Returns True if the URL belongs to a domain that requires agent fetch."""
    host = _domain(url)
    return any(host == d or host.endswith("." + d) for d in LIKELY_NEEDS_AGENT)


_PASS1_AGENT_TIMEOUT = 45  # seconds — short cap so Pass 1 stays fast


def _tf_agent_fast(url: str) -> str:
    """Agent fetch with a shorter timeout for Pass 1 (45s vs 180s for Wave 2)."""
    data, _ = _run_tf(
        ["tinyfish", "agent", "run", "--url", url, "--sync", AGENT_FETCH_GOAL],
        timeout=_PASS1_AGENT_TIMEOUT,
    )
    if not isinstance(data, dict):
        return ""
    for k in ("content", "text", "body", "output", "answer"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v
    results = data.get("results")
    if isinstance(results, list) and results:
        first = results[0]
        if isinstance(first, dict):
            for k in ("text", "content", "body"):
                v = first.get(k)
                if isinstance(v, str) and v.strip():
                    return v
    result = data.get("result")
    if result and data.get("status") == "COMPLETED":
        if isinstance(result, str) and result.strip():
            return result
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False, indent=2)
    return ""


def _process_url_free(
    url: str, snippet: str, target_id: int,
    idempotency_key: str, run_id: int,
    ctx: RunContext,
) -> tuple[str, bool]:
    """Pass 1: smart fetch — uses agent (fast, 45s cap) for social/paywalled domains.
    Falls back to free fetch if credits exhausted or agent times out.
    Returns (result, bot_blocked) where bot_blocked=True means Wave 2 should retry."""
    if ctx.should_stop:
        return "stop", False

    content = ""
    used_agent = False

    if _needs_agent(url) and _agent_can_consume(run_id):
        try:
            content = _tf_agent_fast(url)
            used_agent = True
        except Exception:
            # Timeout or error — fall through to free fetch
            logger.debug("scrape.agent_fast_failed", url=url[:80])

    # Free fetch fallback: agent credits exhausted, timed out, or not a social domain
    if not content:
        content = _tf_fetch(url)

    bot_blocked = "bot_blocked" in (content or "").lower() or "target_http_error" in (content or "").lower()

    # If agent worked but got bot-blocked, don't count the credit (refund)
    if used_agent and bot_blocked:
        try:
            import redis as _redis
            r = _redis.Redis.from_url(settings.redis_url, socket_timeout=2)
            r.decr(f"run:{run_id}:agent_used")
        except Exception:
            pass

    full = f"{snippet}\n\n{content}".strip() if snippet else content
    if not full.strip() or len(full.strip()) < 200:
        return "skip", bot_blocked

    saved, _ = _save_post_sync(target_id, url, full, idempotency_key, run_id)
    return ("new" if saved else "dup"), False


# ── Pass 2: agent-only on known_urls (+ any bot-blocked URLs) ────────────

def _process_url_agent(
    url: str, target_id: int, idempotency_key: str, run_id: int,
) -> str:
    """Pass 2: agent fetch. Used only when Pass 1 found 0 posts."""
    content = _tf_agent(url)
    if not content or len(content.strip()) < 200:
        return "skip"

    saved, _ = _save_post_sync(target_id, url, content, idempotency_key, run_id)
    return "new" if saved else "dup"


# ── Main scrape service ───────────────────────────────────

class ScrapeService:
    def __init__(self) -> None:
        pass

    def scrape(self, target_id: int, ctx: RunContext, idempotency_key: str) -> dict:
        return _run_in_thread(self._run(target_id, ctx, idempotency_key))

    async def _run(self, target_id: int, ctx: RunContext, idempotency_key: str) -> dict:
        from app.database import CelerySessionLocal
        from app.models import Target

        async with CelerySessionLocal() as sess:
            target = await sess.get(Target, target_id)
            if not target:
                return {"error": "target_not_found"}
            name = target.name
            import json as _json
            known_urls: list[str] = _json.loads(target.known_urls or "[]")

        ids = extract_identifiers(known_urls)
        run_id = ctx.run_id

        # ── Pass 1: parallel search + parallel fetch ───────────────────────
        logger.info("scrape.pass1.start", target=name)
        queries = build_search_queries(name, ids)

        # Build candidate list: known_urls (score=10) + search results
        seen_urls: set[str] = set()
        candidates: list[dict] = []
        lock = threading.Lock()

        # Always include known_urls in Pass 1 — free fetch even if they might bot-block
        for ku in (known_urls or []):
            if ku and not _is_binary(ku):
                seen_urls.add(ku)
                candidates.append({"url": ku, "snippet": "", "score": 10})

        # Search queries in parallel
        def _do_search(q: str):
            for hit in _tf_search(q):
                url = hit.get("url", "")
                if not url or _is_binary(url):
                    return
                with lock:
                    if url not in seen_urls:
                        seen_urls.add(url)
                        candidates.append({
                            "url": url,
                            "snippet": hit.get("snippet", ""),
                            "score": _signal_score(url, ids),
                        })

        with ThreadPoolExecutor(max_workers=_SEARCH_WORKERS) as ex:
            list(ex.map(_do_search, queries))

        candidates.sort(key=lambda c: c["score"], reverse=True)
        n_known = len([k for k in (known_urls or []) if k and not _is_binary(k)])
        top_candidates = [c for c in candidates if not ctx.should_stop][: 10 + n_known]

        # ── Pass 1: FREE FETCH ONLY on all candidates (no agent) ─────────────
        # Track bot-blocked URLs for potential Pass 2 agent retry
        new_posts = 0
        duplicates = 0
        bot_blocked_urls: list[str] = []

        with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as ex:
            futures = {
                ex.submit(
                    _process_url_free,
                    c["url"], c["snippet"],
                    target_id, idempotency_key, run_id,
                    ctx,
                ): c["url"]
                for c in top_candidates
            }
            for future in as_completed(futures):
                result, blocked = future.result()
                if result == "new":
                    new_posts += 1
                elif result == "dup":
                    duplicates += 1
                if blocked:
                    bot_blocked_urls.append(futures[future])

        logger.info("scrape.pass1.done", target=name, new=new_posts, dupes=duplicates,
                    candidates=len(candidates), bot_blocked=len(bot_blocked_urls))

        # Wave 1 ends here — NO agent calls.
        # If 0 posts, the wave2_rescue Celery task will handle it after all targets finish.
        return {
            "new_posts":      new_posts,
            "duplicates":     duplicates,
            "needs_rescue":   new_posts == 0,
            "bot_blocked":    bot_blocked_urls,
        }

    # ── Wave 2 rescue (called from wave2_rescue Celery task) ─────────────

    def rescue(self, target_id: int, ctx: RunContext,
               idempotency_key: str, bot_blocked_urls: list[str] | None = None) -> dict:
        """Agent-only rescue for a 0-post target from Wave 1.
        Tries known_urls + any bot-blocked URLs from Wave 1."""
        return _run_in_thread(self._rescue_async(target_id, ctx, idempotency_key, bot_blocked_urls or []))

    async def _rescue_async(self, target_id: int, ctx: RunContext,
                            idempotency_key: str, bot_blocked: list[str]) -> dict:
        from app.database import CelerySessionLocal
        from app.models import Target
        import json as _json

        async with CelerySessionLocal() as sess:
            target = await sess.get(Target, target_id)
            if not target:
                return {"error": "target_not_found"}
            name = target.name
            known_urls: list[str] = _json.loads(target.known_urls or "[]")

        ids = extract_identifiers(known_urls)

        # Deduplicated list: known_urls (highest signal) + bot-blocked from Wave 1
        agent_targets = list(dict.fromkeys(
            [u for u in known_urls if u and not _is_binary(u)] + bot_blocked
        ))[:5]  # cap at 5 agent calls per target

        rescued = 0
        loop = asyncio.get_running_loop()
        logger.info("scrape.wave2.start", target=name, urls=len(agent_targets))
        for url in agent_targets:
            if ctx.should_stop or _AGENT_CREDITS_EXHAUSTED or not _agent_can_consume(ctx.run_id):
                if _AGENT_CREDITS_EXHAUSTED:
                    logger.info("scrape.wave2.skip_credits_exhausted", target=name)
                break
            result = await loop.run_in_executor(
                None, _process_url_agent, url, target_id, idempotency_key, ctx.run_id
            )
            if result == "new":
                rescued += 1

        logger.info("scrape.wave2.done", target=name, rescued=rescued)
        return {"rescue_posts": rescued, "needs_rescue": rescued == 0}

    def rescue_scrape(self, target_id: int, ctx: RunContext) -> dict:
        """Legacy entry point kept for backwards compat."""
        return self.rescue(target_id, ctx, f"standalone_{ctx.run_id}")

    # ── Pass 3: re-extract from stored posts (extended window) ────────────

    async def _extended_window(
        self, target_id: int, name: str, run_id: int, ctx: RunContext,
    ) -> int:
        from app.database import CelerySessionLocal
        from app.models import ScrapedPost, ExtractedInsight
        from sqlalchemy import select

        async with CelerySessionLocal() as sess:
            all_posts = await sess.execute(
                select(ScrapedPost).where(ScrapedPost.target_id == target_id)
            )
            posts = all_posts.scalars().all()
            if not posts:
                return 0
            candidates = []
            for p in posts:
                existing = await sess.execute(
                    select(ExtractedInsight)
                    .where(ExtractedInsight.scraped_post_id == p.id).limit(1)
                )
                if existing.scalar_one_or_none() is None:
                    candidates.append(p)

        if not candidates:
            return 0
        candidates = candidates[:8]
        logger.info("scrape.extended.start", target=name, posts=len(candidates))

        from app.tasks.llm import extract_insights as extract_task
        for post in candidates:
            if ctx.should_stop:
                break
            if post.raw_content:
                extract_task.apply_async(args=[post.id, run_id])

        return len(candidates)

