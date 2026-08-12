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

from app.services.fr_sources import Scope, fr_account_groups
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


def _account_groups(platform: str, accounts: list[str] | None) -> list[str]:
    """`site:` groups for the accounts the team tracks.

    `accounts` comes from the tracked_accounts table. When the caller passes
    nothing (a unit test, or a path that has no session) the curated constant in
    fr_sources is used, so search never silently loses its France pinning.
    """
    if accounts is None:
        return fr_account_groups(platform)
    if platform != "twitter" or not accounts:
        return []
    handles = [h.strip().lstrip("@") for h in accounts if h and h.strip()]
    groups = []
    for i in range(0, len(handles), 5):
        batch = handles[i:i + 5]
        groups.append("(" + " OR ".join(f"site:x.com/{h}" for h in batch) + ")")
    return groups


def _search_variants(platform: str, term: str, lang_filter: str | None,
                     accounts: list[str] | None = None) -> list[str]:
    """Search strings to issue for one term.

    TinyFish ``search query`` hits a *web* index, not the platform's own search,
    so X's native ``lang:fr`` operator does not apply here — it would be matched
    as literal text. (That operator is real, but only on the Apify Twitter actor
    path in ``apify_client``.)

    A social post has no `.fr` domain to pin, so under the French scope the
    SOURCE is the account: X searches are scoped to verified French accounts
    (`site:x.com/<handle>`, batched so N accounts cost one search), and LinkedIn
    uses its real country locale, `fr.linkedin.com`.

    This previously appended the literal word "France" to the query. That is a
    content test, not a source test — the same defect removed from
    routers/discovery._localize — and it measured 0/10 French on X.

    One unpinned variant is kept on purpose. Pinning every query to the registry
    would mean the only authors ever seen are ones already registered, which
    silently kills Emerging Voices (it surfaces authors *not* yet tracked) — the
    one mechanism for growing the registry.

    Extra variants are free: under the TinyFish plan, search and fetch are not
    metered — only agent runs consume credits.
    """
    fr = lang_filter == "fr"
    if platform == "twitter":
        # Unpinned discovery lane first, then the account-pinned lanes.
        variants = [f"{term} site:twitter.com OR site:x.com"]
        if fr:
            variants += [f"{term} {group}" for group in _account_groups("twitter", accounts)]
        return variants
    # LinkedIn: fr.linkedin.com is a real country subdomain, so it pins the
    # source directly (measured 10/10 French). The generic domain stays as a
    # discovery lane for French posts hosted on the global locale.
    if fr:
        return [f"{term} site:fr.linkedin.com", f"{term} site:linkedin.com"]
    return [f"{term} site:linkedin.com"]


def fetch_via_tinyfish(platform: str, queries: list[str],
                      max_results: int = 30,
                      lang_filter: str | None = "fr",
                      accounts: list[str] | None = None) -> list[dict]:
    """Run TinyFish searches for each query, return SocialPost-shaped dicts.

    ``lang_filter="fr"`` targets the search at the French market rather than
    searching worldwide and filtering afterwards. Filtering at display can only
    subtract — if 5% of a worldwide haul is French, the French view is 5% of it.
    Searching in French fills the same result slots with French posts instead.

    Posts are still stored regardless of detected language; this changes what we
    go looking for, not what we keep.

    Engagement counts (likes/comments) are 0 — search results don't include them.
    posted_at is also None — would need to fetch each post individually.
    """
    if platform not in ("twitter", "linkedin"):
        return []

    out: list[dict] = []
    seen_urls: set[str] = set()
    now = datetime.now(timezone.utc)

    # Expand every term to its variants up front, preserving order and dropping
    # duplicates so the same search is never paid for (in wall-clock) twice.
    search_queries: list[str] = []
    for q in queries:
        q_clean = q.strip()
        if not q_clean:
            continue
        for variant in _search_variants(platform, q_clean, lang_filter, accounts):
            if variant not in search_queries:
                search_queries.append(variant)

    search_scope = Scope.FR.value if lang_filter == "fr" else Scope.GLOBAL.value
    for full_q in search_queries:
        try:
            hits = _tf_search_discovery(full_q, scope=search_scope)
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

    logger.info("tinyfish_social.done", platform=platform, terms=len(queries),
                searches=len(search_queries), results=len(out), lang=lang_filter)
    return out[:max_results]
