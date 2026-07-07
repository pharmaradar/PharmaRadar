"""Social trends — Apify-scraped social posts ranked by engagement + recency.

Scan is manual-trigger (POST /scan). Trends are read from the social_posts
table and ranked here. Per-post LLM descriptions are generated on demand when
the user clicks a trend (POST /describe), then cached on the row.
"""
import json
import math
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, desc, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import SocialPost, SearchHistory, User
from app.auth import get_current_user, require_admin, daily_gen_guard

router = APIRouter(prefix="/api/social", tags=["social"])

# Social moves fast — shorter half-life than the KOL pipeline (7-day vs 5).
_HALF_LIFE_DAYS = 7
_STATUS_KEY = "social_scan:status"


def _engagement(p: SocialPost) -> float:
    """Weighted raw engagement. Comments weighted highest (strongest signal of
    real discussion in medical/patient discourse); views scaled way down."""
    return (p.likes or 0) + 2 * (p.comments or 0) + 1.5 * (p.shares or 0) + 0.05 * (p.views or 0)


def _trend_score(p: SocialPost, now: datetime) -> float:
    when = p.posted_at or p.scraped_at
    age_days = max(0.0, (now - when).total_seconds() / 86400) if when else 30.0
    decay = math.exp(-age_days / _HALF_LIFE_DAYS)
    return round(_engagement(p) * decay, 3)


def _to_out(p: SocialPost, now: datetime) -> dict:
    return {
        "id": p.id,
        "platform": p.platform,
        "post_url": p.post_url,
        "author": p.author,
        "text": (p.text or "")[:2000],
        "thumbnail_url": p.thumbnail_url,
        "likes": p.likes or 0,
        "comments": p.comments or 0,
        "views": p.views or 0,
        "shares": p.shares or 0,
        "hashtags": json.loads(p.hashtags) if p.hashtags else [],
        "topic": p.topic or p.query or "other",
        "kind": p.kind,
        "posted_at": p.posted_at.isoformat() if p.posted_at else None,
        "trend_score": _trend_score(p, now),
        "has_description": bool(p.llm_description),
        "language": p.language or "en",
    }


# ── Scan trigger + status ─────────────────────────────────

@router.post("/scan")
async def trigger_scan(lang: str | None = None):
    """Kick off a manual social trend scan via Apify.
    `lang` overrides settings.social_lang_filter for this scan only."""
    from app.services import apify_client
    if not apify_client.is_configured():
        raise HTTPException(status_code=400, detail="APIFY_API_TOKEN not configured")

    try:
        import redis as _redis
        from app.config import get_settings
        r = _redis.Redis.from_url(get_settings().redis_url, socket_timeout=2)
        cur = r.get(_STATUS_KEY)
        if cur and json.loads(cur).get("running"):
            raise HTTPException(status_code=409, detail="A scan is already running")
    except HTTPException:
        raise
    except Exception:
        pass

    from app.tasks.social import social_scan
    task = social_scan.delay(lang)
    return {"started": True, "task_id": task.id, "lang": lang}


@router.delete("/posts", dependencies=[Depends(require_admin)])
async def clear_posts(db: AsyncSession = Depends(get_db)):
    """Delete all social posts. Admin only — destructive, forces a paid re-scrape."""
    from sqlalchemy import delete, func
    count_q = await db.execute(select(func.count()).select_from(SocialPost))
    before = count_q.scalar() or 0
    await db.execute(delete(SocialPost))
    await db.commit()
    return {"deleted": before}


@router.get("/status")
async def scan_status():
    try:
        import redis as _redis
        from app.config import get_settings
        r = _redis.Redis.from_url(get_settings().redis_url, socket_timeout=2)
        cur = r.get(_STATUS_KEY)
        if cur:
            return json.loads(cur)
    except Exception:
        pass
    return {"running": False}


# ── Trends read ───────────────────────────────────────────

@router.get("/trends")
async def trends(
    days: int = 180,
    platform: str | None = None,
    kind: str | None = None,
    limit: int = 60,
    language: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    if from_date:
        try:
            from datetime import date as _date
            since = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    if to_date:
        try:
            until = datetime.fromisoformat(to_date).replace(tzinfo=timezone.utc) + timedelta(days=1)
        except ValueError:
            until = now
    else:
        until = now

    q = select(SocialPost).where(SocialPost.scraped_at >= since, SocialPost.scraped_at <= until)
    if platform and platform != "all":
        q = q.where(SocialPost.platform == platform)
    if kind and kind != "all":
        q = q.where(SocialPost.kind == kind)
    if language and language != "all":
        q = q.where(SocialPost.language == language)
    # Pull a generous slice then rank in Python (engagement+recency isn't SQL-cheap)
    q = q.order_by(desc(SocialPost.scraped_at)).limit(1000)

    rows = await db.execute(q)
    posts = rows.scalars().all()

    ranked = sorted(posts, key=lambda p: _trend_score(p, now), reverse=True)
    top_posts = [_to_out(p, now) for p in ranked[:limit]]

    # Aggregate trending topics (group by topic/query)
    topic_agg: dict[str, dict] = {}
    for p in posts:
        key = p.topic or p.query or "other"
        a = topic_agg.setdefault(key, {"topic": key, "count": 0, "engagement": 0.0,
                                        "score": 0.0, "platforms": set()})
        a["count"] += 1
        a["engagement"] += _engagement(p)
        a["score"] += _trend_score(p, now)
        a["platforms"].add(p.platform)

    top_topics = sorted(topic_agg.values(), key=lambda a: a["score"], reverse=True)[:15]
    for a in top_topics:
        a["platforms"] = sorted(a["platforms"])
        a["engagement"] = int(a["engagement"])
        a["score"] = round(a["score"], 3)

    return {
        "period_days": days,
        "total": len(posts),
        "top_posts": top_posts,
        "top_topics": top_topics,
    }


# ── Synthesis / takeaway + LLM editor's picks ─────────────

@router.get("/synthesis")
async def synthesis(days: int = 30, lang: str | None = None, refresh: bool = False,
                    db: AsyncSession = Depends(get_db),
                    user=Depends(daily_gen_guard("social_synthesis"))):
    """On-demand LLM synthesis of the recent social feed (filter-independent).

    Returns a takeaway + 'so what for Roche' + the LLM's pick of the most
    interesting/impactful posts (each with a one-line why). Cached 6h in Redis.
    """
    from app.config import get_settings
    from app.services.synthesizer import parse_synthesis

    key = f"social_synth:v1:{days}:{lang or 'all'}"
    ukey = f"{key}:u{user.id}"   # private regenerate per user; shared key untouched
    r = None
    try:
        import redis as _redis
        r = _redis.Redis.from_url(get_settings().redis_url, socket_timeout=2)
        if not refresh:
            cached = r.get(ukey) or r.get(key)
            if cached:
                return json.loads(cached)
    except Exception:
        r = None

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    q = select(SocialPost).where(SocialPost.scraped_at >= since)
    if lang and lang != "all":
        q = q.where(SocialPost.language == lang)
    q = q.order_by(desc(SocialPost.scraped_at)).limit(1000)
    rows = await db.execute(q)
    posts = rows.scalars().all()

    empty = {"takeaway": "", "so_what": "", "highlights": [],
             "total_posts": 0, "generated_at": None, "cached": False, "error": None}
    if not posts:
        return empty

    ranked = sorted(posts, key=lambda p: _trend_score(p, now), reverse=True)
    sample = ranked[:50]
    listing = "\n".join(
        f"[{p.id}] ({p.platform}, {p.likes}♥ {p.comments}\U0001f4ac) "
        f"{p.author or ''}: \"{(p.text or '')[:200]}\""
        for p in sample
    )

    prompt = (
        "You are a senior pharma social-media intelligence analyst for Roche France.\n"
        f"Below are {len(sample)} of the most-engaged social posts from the last {days} days "
        "(Instagram, X, LinkedIn, Facebook) on oncology / medical / Roche topics. "
        "Each line is prefixed with its [id].\n\n"
        f"{listing}\n\n"
        "Write a concise intelligence synthesis. Use EXACTLY this format with these markers:\n"
        "##TAKEAWAY##\n"
        "3-5 sentences: what the social conversation centres on right now, notable shifts, "
        "competitor or drug mentions, sentiment.\n"
        "##SO_WHAT##\n"
        "2-3 sentences on what this means for Roche France and what to monitor or act on.\n"
        "##CONCLUSION##\n"
        "2-3 sentences: the bottom line — the single most important thing to focus on now.\n"
        "##PICKS##\n"
        "The 4-6 most interesting / impactful posts. One per line, format: [id] one-sentence why it matters. "
        "Use the real [id] values from the list above.\n\n"
        "Reference real drug names, hashtags and platforms. Be specific."
    )

    from app.services.llm_router import call_pro
    err = None
    parsed = {"takeaway": "", "so_what": "", "picks": []}
    try:
        raw = call_pro([{"role": "user", "content": prompt}], max_tokens=2500)
        parsed = parse_synthesis(raw)
    except Exception as exc:
        err = str(exc)[:300]

    by_id = {p.id: p for p in posts}
    highlights = []
    for pick in parsed["picks"][:6]:
        p = by_id.get(pick["id"])
        if p:
            out = _to_out(p, now)
            out["why"] = pick["why"]
            highlights.append(out)

    result = {
        "takeaway": parsed["takeaway"],
        "so_what": parsed["so_what"],
        "conclusion": parsed["conclusion"],
        "highlights": highlights,
        "total_posts": len(posts),
        "generated_at": now.isoformat(),
        "cached": False,
        "error": err,
    }
    try:
        if r and (parsed["takeaway"] or highlights):
            r.set(ukey if refresh else key, json.dumps(result), ex=21600)
    except Exception:
        pass
    return result


# ── Time series for the trend wave chart ──────────────────

@router.get("/timeseries")
async def timeseries(days: int = 180, top: int = 6, db: AsyncSession = Depends(get_db)):
    """Weekly engagement per top-N topic over the window — feeds the dashboard
    wave chart. Each series point is total engagement for that topic that week."""
    from collections import defaultdict

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    rows = await db.execute(
        select(SocialPost).where(SocialPost.scraped_at >= since).limit(5000)
    )
    posts = rows.scalars().all()
    if not posts:
        return {"topics": [], "series": []}

    # Rank topics by total engagement, take top N
    topic_eng: dict[str, float] = defaultdict(float)
    for p in posts:
        topic_eng[p.topic or p.query or "other"] += _engagement(p)
    top_topics = [t for t, _ in sorted(topic_eng.items(), key=lambda kv: kv[1], reverse=True)[:top]]
    topic_set = set(top_topics)

    # Bucket weekly (Monday-start) → {week_iso: {topic: engagement}}
    buckets: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for p in posts:
        topic = p.topic or p.query or "other"
        if topic not in topic_set:
            continue
        when = p.posted_at or p.scraped_at
        if not when:
            continue
        week_start = (when - timedelta(days=when.weekday())).date().isoformat()
        buckets[week_start][topic] += _engagement(p)

    series = []
    for week in sorted(buckets.keys()):
        point: dict = {"date": week}
        for t in top_topics:
            point[t] = round(buckets[week].get(t, 0.0), 1)
        series.append(point)

    return {"topics": top_topics, "series": series}


# ── Discovery: cached matches + background fresh Apify fetch ──

@router.get("/discover")
async def discover(q: str, fresh: bool = True, lang: str | None = None,
                   db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Return social posts matching a query, ranked. `lang` overrides the
    configured default (fr/en/all). If user picks "Global" in the UI, lang="all"."""
    term = (q or "").strip()
    if len(term) < 2:
        return {"query": term, "results": [], "fetching": False}

    db.add(SearchHistory(user_id=user.id, kind="social", query=term))
    await db.commit()

    now = datetime.now(timezone.utc)
    like = f"%{term.lower()}%"
    base = select(SocialPost).where(or_(
        func.lower(SocialPost.text).like(like),
        func.lower(SocialPost.topic).like(like),
        func.lower(SocialPost.query).like(like),
        func.lower(SocialPost.hashtags).like(like),
    ))
    # Filter cached posts by language when not in "all" mode
    if lang and lang != "all":
        base = base.where(SocialPost.language == lang)
    rows = await db.execute(base.order_by(desc(SocialPost.scraped_at)).limit(500))
    posts = rows.scalars().all()
    ranked = sorted(posts, key=lambda p: _trend_score(p, now), reverse=True)
    results = [_to_out(p, now) for p in ranked[:60]]

    fetching = False
    from app.services import apify_client
    if fresh and apify_client.is_configured():
        from app.tasks.social import discover_fetch
        # Pass lang override to the fetch task (None means use settings default)
        discover_fetch.delay(term, lang)
        fetching = True

    return {"query": term, "results": results, "fetching": fetching}


@router.get("/discover/history")
async def discover_history(db: AsyncSession = Depends(get_db),
                           user: User = Depends(get_current_user)):
    """This user's recently-searched Social Trends queries, deduped, latest first."""
    rows = await db.execute(
        select(SearchHistory.query, SearchHistory.created_at)
        .where(SearchHistory.kind == "social", SearchHistory.user_id == user.id)
        .order_by(desc(SearchHistory.created_at))
        .limit(300)
    )
    seen: set = set()
    queries = []
    for q, at in rows:
        if not q or q in seen:
            continue
        seen.add(q)
        queries.append({"query": q, "scraped_at": at.isoformat() if at else ""})
    return {"queries": queries[:20]}


@router.get("/discover/status")
async def discover_status(q: str):
    try:
        import redis as _redis
        from app.config import get_settings
        from app.tasks.social import _DISCOVER_STATUS_KEY
        r = _redis.Redis.from_url(get_settings().redis_url, socket_timeout=2)
        cur = r.get(_DISCOVER_STATUS_KEY.format(q=q.lower().strip()))
        if cur:
            return json.loads(cur)
    except Exception:
        pass
    return {"running": False}


# ── Click-to-describe (LLM, cached) ───────────────────────

class DescribeRequest(BaseModel):
    id: int


_SEPARATOR = "\n\n@@SO_WHAT@@\n\n"


def _split_description(raw: str) -> tuple[str, str | None]:
    """Split stored description into (what, so_what). Handles legacy single-block format."""
    if _SEPARATOR in raw:
        parts = raw.split(_SEPARATOR, 1)
        return parts[0].strip(), parts[1].strip()
    return raw.strip(), None


@router.post("/describe")
async def describe(body: DescribeRequest, db: AsyncSession = Depends(get_db)):
    post = await db.get(SocialPost, body.id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.llm_description and _SEPARATOR in post.llm_description:
        what, so_what = _split_description(post.llm_description)
        return {"description": what, "so_what": so_what, "cached": True}
    # Clear malformed cache (old format or WHAT: prefix leaked in)
    if post.llm_description and post.llm_description.upper().startswith("WHAT:"):
        post.llm_description = None
        await db.commit()

    import asyncio
    from functools import partial
    from app.services.llm_router import call_pro

    hashtags = json.loads(post.hashtags) if post.hashtags else []
    prompt = (
        "You are a pharma intelligence analyst. Analyse this social media post and respond "
        "in exactly two sections with these exact headers:\n\n"
        "WHAT: [2-3 sentences describing what the post is about, who posted it, and the context.]\n\n"
        "SO WHAT FOR PHARMA: [1-2 sentences on the direct implication for a pharma/Roche medical "
        "affairs team — what action, signal, or risk does this represent? Be specific and actionable.]\n\n"
        "Be concrete and factual. Do not speculate beyond the post content.\n\n"
        f"Platform: {post.platform}\n"
        f"Author: {post.author or 'unknown'}\n"
        f"Topic/keyword: {post.topic or '-'}\n"
        f"Hashtags: {', '.join(hashtags) if hashtags else '-'}\n"
        f"Engagement: {post.likes or 0} likes, {post.comments or 0} comments, "
        f"{post.views or 0} views\n\n"
        f"Post text:\n{(post.text or '')[:3000]}"
    )
    messages = [{"role": "user", "content": prompt}]

    loop = asyncio.get_running_loop()
    try:
        reply = await loop.run_in_executor(None, partial(call_pro, messages, max_tokens=900))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {str(exc)[:200]}")

    # Parse WHAT / SO WHAT sections out of the reply
    raw = reply.strip()
    what = raw
    so_what: str | None = None

    upper = raw.upper()
    if "SO WHAT FOR PHARMA:" in upper:
        idx = upper.index("SO WHAT FOR PHARMA:")
        what_part = raw[:idx].strip()
        so_what_part = raw[idx:].split(":", 1)[-1].strip()
        what = what_part
        so_what = so_what_part

    # Strip any "WHAT:" header prefix regardless of whether SO WHAT was found
    if what.upper().startswith("WHAT:"):
        what = what[5:].strip()

    # Store both in one column with separator
    post.llm_description = what + (_SEPARATOR + so_what if so_what else "")
    await db.commit()
    return {"description": what, "so_what": so_what, "cached": False}
