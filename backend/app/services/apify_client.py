"""Apify social-media scraping service.

TinyFish handles the open web; Apify handles social platforms (Instagram, X,
LinkedIn, Facebook) where TinyFish can't reach. Each platform has a purpose-built
Apify Actor whose output we normalize into one common post shape with real
engagement counts (likes / comments / views / shares).

Actor output schemas differ per platform and change over time, so every
normalizer reads each field through a tolerant multi-key lookup and never
raises on a missing key — a malformed item is skipped, not fatal.

Platform notes:
- Twitter: microworlds/twitter-scraper uses browser automation — survives X API lockdowns.
- LinkedIn: requires a LinkedIn session cookie in Apify actor settings (auth-gated).
- Facebook: apify/facebook-search-scraper for keyword search; apify/facebook-posts-scraper
  when curated page_urls are provided (more precise for known pharma pages).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from apify_client import ApifyClient

from app.config import get_settings

logger = structlog.get_logger(__name__)

ACTORS = {
    "instagram": "apify/instagram-hashtag-scraper",
    # microworlds uses browser automation — survives X API lockdowns that kill API-based scrapers
    "twitter":   "microworlds/twitter-scraper",
    # requires LinkedIn session cookie configured in the Apify actor settings
    "linkedin":  "apify/linkedin-post-search-scraper",
    # used only when curated page_urls are provided; keyword fallback uses _ACTOR_FB_SEARCH
    "facebook":  "apify/facebook-posts-scraper",
}
# Keyword-based FB search (no page URLs needed); used when page_urls not configured
_ACTOR_FB_SEARCH = "apify/facebook-search-scraper"

# Instagram needs TWO actors, and which one runs depends on the question asked.
#
# The hashtag actor takes an ARRAY of tags, so every keyword goes out in ONE run —
# cheap, and the right tool for "what is the market saying". But it has no account
# input at all, so it can never answer "what did Gustave Roussy post". The profile
# actor can, and its `directUrls` is also an array, so N tracked accounts still
# cost one run. Keeping both is what makes account tracking possible without
# multiplying the Apify bill.
_ACTOR_IG_PROFILE = "apify/instagram-scraper"
_ACTOR_IG_COMMENTS = "apify/instagram-comment-scraper"

_HASHTAG_RE = re.compile(r"#(\w+)")


# ── value coercion helpers ────────────────────────────────

def _first(item: dict, *keys: str) -> Any:
    """Return the first present, non-None value among keys (supports a.b nesting)."""
    for key in keys:
        if "." in key:
            cur: Any = item
            for part in key.split("."):
                if isinstance(cur, dict):
                    cur = cur.get(part)
                else:
                    cur = None
                    break
            if cur is not None:
                return cur
        elif item.get(key) is not None:
            return item[key]
    return None


def _int(val: Any) -> int:
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    try:
        return int(re.sub(r"[^\d]", "", str(val)) or 0)
    except (ValueError, TypeError):
        return 0


def _parse_dt(val: Any) -> datetime | None:
    """Parse ISO-8601 strings or epoch seconds into a tz-aware UTC datetime."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            return datetime.fromtimestamp(val, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    s = str(val).strip()
    if not s:
        return None
    if s.isdigit():
        return _parse_dt(int(s))
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _hashtags(item: dict, text: str | None) -> list[str]:
    tags = _first(item, "hashtags")
    if isinstance(tags, list) and tags:
        out = []
        for t in tags:
            if isinstance(t, str):
                out.append(t.lstrip("#"))
            elif isinstance(t, dict):
                name = t.get("name") or t.get("text")
                if name:
                    out.append(str(name).lstrip("#"))
        if out:
            return out
    return _HASHTAG_RE.findall(text or "")


# ── per-platform normalizers ──────────────────────────────

def _norm_instagram(item: dict) -> dict | None:
    url = _first(item, "url", "postUrl", "inputUrl")
    if not url:
        return None
    text = _first(item, "caption", "text")
    return {
        "platform": "instagram",
        "post_url": url,
        "author": _first(item, "ownerUsername", "ownerFullName", "owner.username"),
        "text": text,
        "thumbnail_url": _first(item, "displayUrl", "thumbnailUrl", "imageUrl"),
        "likes": _int(_first(item, "likesCount", "likes")),
        "comments": _int(_first(item, "commentsCount", "comments")),
        "views": _int(_first(item, "videoViewCount", "videoPlayCount", "viewsCount")),
        "shares": 0,
        "hashtags": _hashtags(item, text),
        "posted_at": _parse_dt(_first(item, "timestamp", "takenAtTimestamp")),
    }


def _norm_twitter(item: dict) -> dict | None:
    url = _first(item, "url", "twitterUrl", "tweetUrl")
    if not url:
        return None
    text = _first(item, "text", "fullText", "full_text")
    return {
        "platform": "twitter",
        "post_url": url,
        "author": _first(item, "author.userName", "author.username", "username", "userName"),
        "text": text,
        "thumbnail_url": _first(item, "author.profilePicture", "media.0.media_url_https"),
        "likes": _int(_first(item, "likeCount", "favoriteCount", "likes")),
        "comments": _int(_first(item, "replyCount", "replies")),
        "views": _int(_first(item, "viewCount", "views")),
        "shares": _int(_first(item, "retweetCount", "retweets")),
        "hashtags": _hashtags(item, text),
        "posted_at": _parse_dt(_first(item, "createdAt", "created_at", "date")),
    }


def _norm_linkedin(item: dict) -> dict | None:
    url = _first(item, "postUrl", "url", "shareUrl")
    if not url:
        return None
    text = _first(item, "text", "commentary", "content")
    return {
        "platform": "linkedin",
        "post_url": url,
        "author": _first(item, "authorName", "actorName", "author.name", "author.firstName"),
        "text": text,
        "thumbnail_url": _first(item, "image", "thumbnailUrl", "imageUrl"),
        "likes": _int(_first(item, "numLikes", "likesCount", "reactionCount")),
        "comments": _int(_first(item, "numComments", "commentsCount")),
        "views": _int(_first(item, "numImpressions", "impressionCount")),
        "shares": _int(_first(item, "numReposts", "repostsCount", "sharesCount")),
        "hashtags": _hashtags(item, text),
        "posted_at": _parse_dt(_first(item, "postedAt", "createdAt", "publishedAt", "date")),
    }


def _norm_facebook(item: dict) -> dict | None:
    # Handles both apify/facebook-search-scraper and apify/facebook-posts-scraper
    # output schemas (field names differ between the two actors).
    url = _first(item, "postUrl", "url", "link", "topLevelUrl")
    if not url:
        return None
    text = _first(item, "text", "message", "postText", "body")
    return {
        "platform": "facebook",
        "post_url": url,
        "author": _first(item, "pageName", "authorName", "user.name", "groupName"),
        "text": text,
        "thumbnail_url": _first(item, "thumbnailUrl", "image", "media.0.thumbnail", "previewImage"),
        "likes": _int(_first(item, "likesCount", "likes", "reactionsCount")),
        "comments": _int(_first(item, "commentsCount", "comments")),
        "views": _int(_first(item, "viewsCount", "videoViewCount")),
        "shares": _int(_first(item, "sharesCount", "shares")),
        "hashtags": _hashtags(item, text),
        "posted_at": _parse_dt(_first(item, "date", "time", "publishedTime", "createdTime")),
    }


_NORMALIZERS = {
    "instagram": _norm_instagram,
    "twitter":   _norm_twitter,
    "linkedin":  _norm_linkedin,
    "facebook":  _norm_facebook,
}


# ── actor input builders ──────────────────────────────────

# apify/instagram-hashtag-scraper validates every entry against this pattern and
# rejects the WHOLE run with HTTP 400 if any one term fails. Read from the live
# 400 response on 2026-08-12: ^\s*#?[^!?.,:;\-+=*&%$#@/\~^|<>()[\]{}"'`]+$
#
# Accents and spaces are fine ("immunothérapie", "cancer du poumon" both work and
# return French accounts). Punctuation is not — a single hyphen or question mark
# 400s the run, and since fetch_platform_expanded batches every term into one
# call, one bad term returns zero Instagram posts for the entire search.
_IG_FORBIDDEN = re.compile(r"""[!?.,:;\-+=*&%$#@/\\~^|<>()\[\]{}"'`]""")


def sanitize_ig_term(term: str) -> str:
    """Make a term safe for the Instagram actor.

    Forbidden punctuation becomes a space rather than being deleted, because the
    actor accepts spaces: "sous-cutanée" -> "sous cutanée" stays searchable,
    whereas "souscutanée" is a word that does not exist.
    """
    cleaned = _IG_FORBIDDEN.sub(" ", term or "").lstrip("#@")
    return re.sub(r"\s+", " ", cleaned).strip()


def _ig_run_input(terms: list[str], max_results: int) -> dict | None:
    """Build the Instagram actor input, or None when nothing survives sanitising.

    Multi-word terms are sent with `keywordSearch` enabled — that input exists on
    the actor and the codebase never set it. Without it a phrase is matched as a
    literal hashtag, so anything a user actually types ("immunothérapie sous
    cutanée") finds nothing, because no such hashtag exists.
    """
    cleaned = [t for t in (sanitize_ig_term(x) for x in terms) if t]
    if not cleaned:
        return None
    run_input = {"hashtags": cleaned, "resultsType": "posts", "resultsLimit": max_results}
    if any(" " in t for t in cleaned):
        run_input["keywordSearch"] = True
    return run_input


def _build_input(platform: str, term: str, max_results: int, since: str | None,
                 lang_filter: str | None = None) -> dict:
    """Map a search term to the Actor's expected input shape.

    `term` is a hashtag/keyword (field scan) or a handle (KOL scan). For
    hashtag-based actors we strip a leading '#'; for keyword/search actors we
    pass the term as-is.
    """
    tag = re.sub(r"\s+", "", term.lstrip("#@"))
    if platform == "instagram":
        return _ig_run_input([term], max_results) or {}
    if platform == "twitter":
        # Native Twitter operator filters by language at search time
        q = f"{term} lang:{lang_filter}" if lang_filter and lang_filter != "all" else term
        return {"searchTerms": [q], "maxItems": max_results}
    if platform == "linkedin":
        # apify/linkedin-post-search-scraper — keywords as array
        return {"keywords": [term], "resultsLimit": max_results}
    if platform == "facebook":
        # populated by fetch_platform depending on whether page_urls are available
        return {}
    return {}



# ── Instagram: accounts and comments ──────────────────────

def _run_actor(actor_id: str, run_input: dict, token: str, timeout_secs: int,
               max_items: int) -> list[dict]:
    """Run one actor and return its raw dataset items. Never raises."""
    try:
        client = ApifyClient(token)
        run = client.actor(actor_id).call(
            run_input=run_input,
            run_timeout=timedelta(seconds=timeout_secs),
            max_items=max_items,
        )
        dataset_id = getattr(run, "default_dataset_id", None) if run else None
        if isinstance(run, dict):
            dataset_id = run.get("defaultDatasetId") or dataset_id
        if not dataset_id:
            logger.warning("apify.no_dataset", actor=actor_id)
            return []
        return list(client.dataset(dataset_id).iterate_items())
    except Exception as exc:
        logger.warning("apify.actor_failed", actor=actor_id, error=str(exc)[:200])
        return []


def fetch_instagram_accounts(handles: list[str], max_per_account: int = 10,
                             window_days: int = 30, timeout_secs: int = 300) -> list[dict]:
    """Posts from specific Instagram accounts.

    Every post is from a chosen account, so it is on-topic by construction — no
    keyword luck involved. All handles go in one run because `directUrls` is an
    array, and the window is pushed into the request via `onlyPostsNewerThan`
    rather than filtered after the fact, so we stop paying for results we then
    discard.
    """
    token = get_settings().apify_api_token
    cleaned = [h.strip().lstrip("@") for h in (handles or []) if h and h.strip()]
    if not token or not cleaned:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    items = _run_actor(
        _ACTOR_IG_PROFILE,
        {
            "directUrls": [f"https://www.instagram.com/{h}/" for h in cleaned],
            "resultsType": "posts",
            "resultsLimit": max_per_account,
            "onlyPostsNewerThan": cutoff.date().isoformat(),
            "addParentData": False,
        },
        token, timeout_secs, max_per_account * len(cleaned),
    )

    posts = []
    for item in items:
        post = _norm_instagram(item)
        if not post:
            continue
        # The actor honours the date server-side; this only catches items that
        # come back without a timestamp at all.
        if post["posted_at"] and post["posted_at"] < cutoff:
            continue
        post["kind"] = "account"
        posts.append(post)
    logger.info("apify.instagram_accounts", accounts=len(cleaned), posts=len(posts))
    return posts


def fetch_instagram_comments(post_urls: list[str], max_per_post: int = 20,
                             timeout_secs: int = 300) -> list[dict]:
    """Comments under specific Instagram posts.

    This is the client's "patient comment following an Instagram post". Comments
    are the most sensitive material the platform touches: a person describing a
    side effect in a comment is an adverse-event report, so every comment is
    stored through the same AE classification and filtering as any other post —
    see services/ae_filter and the AE backfill sweep.
    """
    token = get_settings().apify_api_token
    urls = [u.strip() for u in (post_urls or []) if u and u.strip()]
    if not token or not urls:
        return []

    items = _run_actor(
        _ACTOR_IG_COMMENTS,
        {
            "directUrls": urls,
            "resultsLimit": max_per_post,
            "includeNestedComments": False,
        },
        token, timeout_secs, max_per_post * len(urls),
    )

    comments = []
    for item in items:
        text = _first(item, "text", "comment")
        if not text or not str(text).strip():
            continue
        parent = _first(item, "postUrl", "inputUrl", "post_url") or ""
        comment_id = _first(item, "id", "commentId") or ""
        # Comments rarely carry their own permalink; synthesise a stable one so
        # the content hash and dedup behave like every other post.
        url = _first(item, "commentUrl", "url") or (
            f"{parent.rstrip('/')}#comment-{comment_id}" if parent and comment_id else "")
        if not url:
            continue
        comments.append({
            "platform": "instagram",
            "post_url": url,
            "parent_url": parent,
            "author": _first(item, "ownerUsername", "owner.username", "username"),
            "text": str(text),
            "thumbnail_url": None,
            "likes": _int(_first(item, "likesCount", "likes")),
            "comments": _int(_first(item, "repliesCount")),
            "views": 0,
            "shares": 0,
            "hashtags": _hashtags(item, str(text)),
            "posted_at": _parse_dt(_first(item, "timestamp", "createdAt")),
            "kind": "comment",
        })
    logger.info("apify.instagram_comments", posts=len(urls), comments=len(comments))
    return comments

# ── public API ────────────────────────────────────────────

def is_configured() -> bool:
    return bool(get_settings().apify_api_token)


def fetch_platform_expanded(
    platform: str,
    hashtags: list[str],
    keywords: list[str],
    max_results: int = 30,
    window_days: int = 180,
    timeout_secs: int = 180,
    page_urls: list[str] | None = None,
    lang_filter: str | None = "fr",
    accounts: list[str] | None = None,
) -> list[dict]:
    """Like fetch_platform but accepts pre-expanded term lists.

    Instagram  — Apify actor, all hashtags in one call.
    Twitter    — TinyFish search (Apify deferred to later).
    LinkedIn   — TinyFish search (Apify deferred to later).
    Facebook   — page_urls scraper when available, else keyword search.
    """
    # Twitter + LinkedIn now use TinyFish search (free under existing licence)
    if platform in ("twitter", "linkedin"):
        from app.services.tinyfish_social import fetch_via_tinyfish
        # Mix hashtags + keywords for broader recall
        terms = [t for t in (keywords + hashtags) if t and t.strip()][:6]
        if not terms:
            return []
        return fetch_via_tinyfish(platform, terms, max_results=max_results,
                                  lang_filter=lang_filter, accounts=accounts)

    token = get_settings().apify_api_token
    if not token:
        logger.warning("apify.no_token")
        return []
    normalizer = _NORMALIZERS.get(platform)
    if not normalizer:
        logger.warning("apify.unknown_platform", platform=platform)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    if platform == "instagram":
        actor_id = ACTORS["instagram"]
        # Actor accepts a list — batch all hashtags in one run (cheaper than N runs)
        run_input = _ig_run_input(hashtags, max_results)
        if run_input is None:
            logger.warning("apify.instagram_no_valid_terms", terms=hashtags[:5])
            return []

    elif platform == "twitter":
        actor_id = ACTORS["twitter"]
        terms_clean = [t.strip() for t in keywords if t.strip()][:4]
        combined = " OR ".join(f'"{t}"' if " " in t else t for t in terms_clean)
        # Append lang:fr filter when lang_filter is set
        if lang_filter and lang_filter != "all":
            combined = f"({combined}) lang:{lang_filter}"
        run_input = {"searchTerms": [combined], "maxItems": max_results}

    elif platform == "linkedin":
        actor_id = ACTORS["linkedin"]
        kw = (keywords[0] if keywords else (hashtags[0] if hashtags else "")).strip()
        if not kw:
            return []
        run_input = {"keywords": [kw], "resultsLimit": max_results}

    elif platform == "facebook":
        if page_urls:
            actor_id = ACTORS["facebook"]
            run_input = {
                "startUrls": [{"url": u} for u in page_urls],
                "resultsLimit": max_results,
                "scrapeAbout": False, "scrapeReviews": False, "scrapeServices": False,
            }
        else:
            actor_id = _ACTOR_FB_SEARCH
            kw = (keywords[0] if keywords else (hashtags[0] if hashtags else "")).strip()
            if not kw:
                return []
            run_input = {"searchQuery": kw, "maxResults": max_results, "searchType": "posts"}
    else:
        logger.warning("apify.unknown_platform", platform=platform)
        return []

    try:
        client = ApifyClient(token)
        run = client.actor(actor_id).call(
            run_input=run_input,
            run_timeout=timedelta(seconds=timeout_secs),
            max_items=max_results,
        )
        dataset_id = getattr(run, "default_dataset_id", None) if run else None
        if not dataset_id:
            logger.warning("apify.no_dataset", platform=platform)
            return []
        raw_items = client.dataset(dataset_id).list_items(limit=max_results).items
    except Exception as exc:
        logger.warning("apify.run_failed_expanded", platform=platform, exc=str(exc)[:200])
        return []

    posts: list[dict] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        try:
            post = normalizer(raw)
        except Exception:
            continue
        if not post:
            continue
        if post["posted_at"] and post["posted_at"] < cutoff:
            continue
        posts.append(post)

    logger.info("apify.fetched_expanded", platform=platform, count=len(posts))
    return posts


def fetch_platform(platform: str, term: str, max_results: int = 30,
                   window_days: int = 180, timeout_secs: int = 180,
                   page_urls: list[str] | None = None,
                   lang_filter: str | None = "fr",
                   accounts: list[str] | None = None) -> list[dict]:
    """Run one platform Actor.
    Returns normalized posts filtered to the last `window_days`. Never raises."""
    # Twitter + LinkedIn use TinyFish search (Apify deferred)
    if platform in ("twitter", "linkedin"):
        from app.services.tinyfish_social import fetch_via_tinyfish
        return fetch_via_tinyfish(platform, [term], max_results=max_results,
                                  lang_filter=lang_filter, accounts=accounts)

    token = get_settings().apify_api_token
    if not token:
        logger.warning("apify.no_token")
        return []
    normalizer = _NORMALIZERS.get(platform)
    if not normalizer:
        logger.warning("apify.unknown_platform", platform=platform)
        return []

    # Resolve actor and input per platform
    if platform == "facebook":
        if page_urls:
            # Curated page scraping — high-quality pharma pages
            actor_id = ACTORS["facebook"]
            run_input = {
                "startUrls": [{"url": u} for u in page_urls],
                "resultsLimit": max_results,
                "scrapeAbout": False,
                "scrapeReviews": False,
                "scrapeServices": False,
            }
        elif term:
            # Keyword search fallback — works without page URLs
            actor_id = _ACTOR_FB_SEARCH
            run_input = {"searchQuery": term, "maxResults": max_results, "searchType": "posts"}
        else:
            logger.info("apify.facebook_skipped_no_urls_no_term")
            return []
    else:
        actor_id = ACTORS.get(platform)
        if not actor_id:
            logger.warning("apify.unknown_platform", platform=platform)
            return []
        run_input = _build_input(platform, term, max_results,
                                 (datetime.now(timezone.utc) - timedelta(days=window_days)).date().isoformat(),
                                 lang_filter=lang_filter)

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    try:
        client = ApifyClient(token)
        run = client.actor(actor_id).call(
            run_input=run_input,
            run_timeout=timedelta(seconds=timeout_secs),
            max_items=max_results,
        )
        dataset_id = getattr(run, "default_dataset_id", None) if run else None
        if not dataset_id:
            logger.warning("apify.no_dataset", platform=platform, term=term)
            return []
        raw_items = client.dataset(dataset_id).list_items(limit=max_results).items
    except Exception as exc:
        logger.warning("apify.run_failed", platform=platform, term=term, exc=str(exc)[:200])
        return []

    posts: list[dict] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        try:
            post = normalizer(raw)
        except Exception:
            continue
        if not post:
            continue
        # Window filter (skip only when we actually know the date)
        if post["posted_at"] and post["posted_at"] < cutoff:
            continue
        posts.append(post)

    logger.info("apify.fetched", platform=platform, term=term, count=len(posts))
    return posts
