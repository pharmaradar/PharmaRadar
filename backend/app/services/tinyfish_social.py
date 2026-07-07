"""TinyFish-based fallback scrapers for Twitter and LinkedIn social posts.

Apify is paid; TinyFish (already used by Discovery) is in-budget. Engagement
counts are not available via search results — we save 0 for likes/comments
and rely on freshness + content match for ranking.
"""
from __future__ import annotations

import hashlib
import re
import structlog
from datetime import datetime, timezone
from urllib.parse import urlparse

from app.services.scraper import _tf_search_discovery

logger = structlog.get_logger(__name__)


def _norm_url(u: str) -> str:
    return u.strip().rstrip("/")


def _extract_handle(url: str) -> str | None:
    """Pull @handle from a twitter/x or linkedin URL."""
    try:
        p = urlparse(url)
        host = (p.netloc or "").lower().replace("www.", "")
        path = (p.path or "").strip("/").split("/")
        if not path:
            return None
        if host in ("twitter.com", "x.com"):
            return f"@{path[0]}" if path[0] not in ("status", "i", "search") else None
        if host == "linkedin.com":
            if len(path) >= 2 and path[0] in ("in", "company"):
                return path[1]
        return None
    except Exception:
        return None


def _is_post_url(platform: str, url: str) -> bool:
    """Filter search results down to actual post URLs."""
    if not url:
        return False
    u = url.lower()
    if platform == "twitter":
        return "/status/" in u and ("twitter.com" in u or "x.com" in u)
    if platform == "linkedin":
        # LinkedIn post or activity URLs
        return ("linkedin.com/posts/" in u or
                "linkedin.com/feed/update/" in u or
                "linkedin.com/pulse/" in u)
    return False


def _hash_url(url: str) -> str:
    return hashlib.sha256(_norm_url(url).encode()).hexdigest()


def fetch_via_tinyfish(platform: str, queries: list[str],
                      max_results: int = 30,
                      lang_filter: str | None = None) -> list[dict]:
    """Run TinyFish searches for each query, return SocialPost-shaped dicts.

    Always searches worldwide — the language is detected and stored per post,
    and the UI filters by language at display time. This avoids paying twice
    if the user later switches from French to Global.

    Engagement counts (likes/comments) are 0 — search results don't include them.
    posted_at is also None — would need to fetch each post individually.
    """
    if platform not in ("twitter", "linkedin"):
        return []

    site_filter = "site:twitter.com OR site:x.com" if platform == "twitter" else "site:linkedin.com"
    out: list[dict] = []
    seen_urls: set[str] = set()
    now = datetime.now(timezone.utc)

    for q in queries:
        q_clean = q.strip()
        if not q_clean:
            continue
        # Build worldwide search query (no lang: operator)
        full_q = f"{q_clean} {site_filter}"

        try:
            hits = _tf_search_discovery(full_q)
        except Exception as exc:
            logger.warning("tinyfish_social.search_failed", platform=platform, q=full_q[:80], exc=str(exc)[:120])
            continue

        for hit in hits or []:
            url = hit.get("url", "")
            if not _is_post_url(platform, url):
                continue
            norm = _norm_url(url)
            if norm in seen_urls:
                continue
            seen_urls.add(norm)

            text = (hit.get("snippet") or hit.get("title") or "").strip()
            if not text or len(text) < 10:
                continue

            out.append({
                "platform": platform,
                "post_url": url,
                "author": _extract_handle(url),
                "text": text,
                "thumbnail_url": None,
                "likes": 0,
                "comments": 0,
                "views": 0,
                "shares": 0,
                "hashtags": re.findall(r"#(\w+)", text)[:10],
                "posted_at": None,
                "content_hash": _hash_url(url),
                "_source": "tinyfish",
            })

            if len(out) >= max_results:
                break

        if len(out) >= max_results:
            break

    logger.info("tinyfish_social.done", platform=platform, queries=len(queries),
                results=len(out), lang=lang_filter)
    return out[:max_results]
