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
from sqlalchemy import select, desc, or_, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import SocialPost, SearchHistory, User
from app.auth import get_current_user, require_admin, daily_gen_guard
from app.services.ae_filter import social_not_ae
from app.services.fr_sources import Scope

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

@router.post("/scan", dependencies=[Depends(require_admin)])
async def trigger_scan(lang: str | None = None):
    """Kick off a manual social trend scan via Apify.

    Admin-only: this is the one endpoint that spends real Apify credits, and it
    was previously reachable by any authenticated user while the far cheaper
    DELETE /posts below already required admin.

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
    # Display window defaults to 30 days per client spec. The *scrape* window
    # stays deep (AppSettings.social_window_days) so widening the view here is
    # instant and free — the posts are already in the DB.
    days: int = 30,
    platform: str | None = None,
    kind: str | None = None,
    limit: int = 60,
    # France-first: the platform monitors the French market, so "fr" is the
    # default rather than an opt-in filter. Pass language="all" to widen.
    language: str | None = "fr",
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

    q = select(SocialPost).where(SocialPost.scraped_at >= since, SocialPost.scraped_at <= until).where(social_not_ae())
    if platform and platform != "all":
        q = q.where(SocialPost.platform == platform)
    if kind and kind != "all":
        q = q.where(SocialPost.kind == kind)
    if language and language != "all":
        # "fr" means French SOURCE, not French text. Filtering on the detected
        # language both over- and under-selects: it admitted francophone Quebec
        # accounts and hid French institutions that post in English. source_scope
        # is set from the account at ingest, so it answers the question asked.
        q = (q.where(SocialPost.source_scope == Scope.FR.value) if language == "fr"
             else q.where(SocialPost.language == language))
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
async def synthesis(days: int = 30, lang: str | None = "fr", refresh: bool = False,
                    db: AsyncSession = Depends(get_db),
                    user=Depends(daily_gen_guard("social_synthesis"))):
    """On-demand LLM synthesis of the recent social feed (filter-independent).

    Returns a takeaway + 'so what for Roche' + the LLM's pick of the most
    interesting/impactful posts (each with a one-line why). Cached 6h in Redis.
    """
    from app.config import get_settings
    from app.services.synthesizer import parse_synthesis

    # v2: the default scope changed from all-languages to French, so v1 entries
    # would serve a global synthesis under a French request.
    key = f"social_synth:v2:{days}:{lang or 'all'}"
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
    q = select(SocialPost).where(SocialPost.scraped_at >= since).where(social_not_ae())
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

    from app.services.llm_router import call_llm_async
    err = None
    parsed = {"takeaway": "", "so_what": "", "picks": []}
    try:
        raw = await call_llm_async([{"role": "user", "content": prompt}], max_tokens=8192)
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
async def timeseries(days: int = 30, top: int = 6, db: AsyncSession = Depends(get_db)):
    """Weekly engagement per top-N topic over the window — feeds the dashboard
    wave chart. Each series point is total engagement for that topic that week."""
    from collections import defaultdict

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    rows = await db.execute(
        select(SocialPost).where(SocialPost.scraped_at >= since).where(social_not_ae()).limit(5000)
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
async def discover(q: str, fresh: bool = True, lang: str | None = "fr",
                   limit: int = 120, force: bool = False,
                   db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Return social posts matching a query, ranked.

    `lang` defaults to French — the platform monitors the French market, so a
    global search is the opt-in, not the default. Pass lang="all" to widen."""
    term = (q or "").strip()
    if len(term) < 2:
        return {"query": term, "results": [], "fetching": False}

    db.add(SearchHistory(user_id=user.id, kind="social", query=term))
    await db.commit()

    now = datetime.now(timezone.utc)

    # A typed question never appears verbatim inside a post, so matching the
    # whole string finds nothing — "does KOL think subcutaneous is better than
    # IV" scored 0 rows against a corpus that does discuss it. Anything longer
    # than a couple of words is treated as a question and broken into terms
    # (bilingual, because the corpus is French and questions usually are not).
    # Short queries stay literal: they are already a term, and expansion would
    # cost an LLM call to tell us so.
    terms = [term]
    if len(term.split()) >= 3:
        try:
            from app.services.question import expand_for_search
            expanded = expand_for_search(term, language=(lang or "fr"))
            if expanded:
                terms = list(dict.fromkeys([term] + expanded))
        except Exception:                        # noqa: BLE001 - never fail the search
            pass

    # `topic` and `query` record HOW a post was collected, not what it says —
    # `query` literally stores the search that retrieved it. Matching a typed
    # question against those is a false-positive machine: the term "KOL" hit a
    # post about spinal muscular atrophy purely because it had been collected
    # under an older search called "what do kol think about evrysdi".
    # Provenance is still worth matching for a short literal query (finding
    # everything gathered under a topic is a real use), so it is only the
    # expanded question path that restricts itself to content.
    def _match_clause(candidates: list[str], content_only: bool):
        conditions = []
        for candidate in candidates:
            like = f"%{candidate.lower()}%"
            conditions.append(func.lower(SocialPost.text).like(like))
            conditions.append(func.lower(SocialPost.hashtags).like(like))
            if content_only:
                # `topic` is a subject label for keyword-collected posts
                # ("Evrysdi") but provenance for account-collected ones
                # ("account:msd-france") — a question mentioning France would
                # otherwise pull in every post from that account.
                conditions.append(and_(
                    func.lower(SocialPost.topic).like(like),
                    ~func.lower(func.coalesce(SocialPost.topic, "")).like("account:%"),
                ))
            else:
                conditions.append(func.lower(SocialPost.topic).like(like))
                conditions.append(func.lower(SocialPost.query).like(like))
        return or_(*conditions)

    # Expansion yields discriminating terms ("sous-cutané") alongside filler the
    # question happened to contain ("better", "think"). Filler matches almost
    # every post, so searching everything at once buries the on-topic results.
    # Search the specific terms first and only widen if that came back thin.
    search_terms = terms
    if len(terms) > 1:
        from app.services.question import split_by_specificity
        specific, _fallback = split_by_specificity(terms)
        if specific:
            search_terms = specific

    expanded_mode = len(terms) > 1
    base = (select(SocialPost)
            .where(_match_clause(search_terms, expanded_mode))
            .where(social_not_ae()))
    # Filter cached posts by source when not in "all" mode — see the note in
    # `trends`: French source, not French text.
    if lang and lang != "all":
        base = (base.where(SocialPost.source_scope == Scope.FR.value) if lang == "fr"
                else base.where(SocialPost.language == lang))
    scoped = base
    rows = await db.execute(scoped.order_by(desc(SocialPost.scraped_at)).limit(1000))
    posts = rows.scalars().all()

    # Too thin to be useful — widen to every term rather than show an empty page.
    if len(posts) < 5 and len(search_terms) < len(terms):
        widened = (select(SocialPost)
                   .where(_match_clause(terms, expanded_mode))
                   .where(social_not_ae()))
        if lang and lang != "all":
            widened = (widened.where(SocialPost.source_scope == Scope.FR.value)
                       if lang == "fr" else widened.where(SocialPost.language == lang))
        posts = (await db.execute(
            widened.order_by(desc(SocialPost.scraped_at)).limit(1000))).scalars().all()
        search_terms = terms

    # With many terms, "matched any" is a weak signal on its own — a post
    # hitting six of the question's terms is more on-topic than one hitting a
    # single common word. Rank by how much of the question a post covers, then
    # fall back to the usual engagement/recency score.
    lowered = [t.lower() for t in search_terms if t and t.strip()]

    def _relevance(post) -> int:
        parts = [post.text or "", post.hashtags or ""]
        topic = post.topic or ""
        if not topic.lower().startswith("account:"):
            parts.append(topic)
        if not expanded_mode:
            parts.append(post.query or "")
        haystack = " ".join(parts).lower()
        return sum(1 for t in lowered if t in haystack)

    ranked = sorted(posts, key=lambda p: (_relevance(p), _trend_score(p, now)),
                    reverse=True)
    results = [_to_out(p, now) for p in ranked[:max(1, min(limit, 200))]]

    fetching = False
    cached = False
    from app.services import apify_client
    if fresh and apify_client.is_configured():
        from app.tasks.social import discover_fetch, query_recently_fetched
        # Searching the same phrase again inside the TTL reads what that search
        # already collected. A live fetch costs Apify credit and returns
        # substantially the same posts, so repeating it charges twice for one
        # answer — the whole monthly budget is $5.
        if query_recently_fetched(term) and not force:
            cached = True
        else:
            # Pass lang override to the fetch task (None means use settings default)
            discover_fetch.delay(term, lang)
            fetching = True

    # `terms` is returned so the UI can show what was actually searched for —
    # a question that silently became ten terms is otherwise unexplainable when
    # the results look surprising.
    return {"query": term, "results": results, "fetching": fetching,
            "cached": cached,
            "terms": terms if len(terms) > 1 else [], "total_matched": len(posts)}


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
    # AE posts are invisible everywhere — a direct describe on one 404s like it
    # doesn't exist (its id can only come from stale/hand-crafted requests).
    if not post or post.is_adverse_event is True:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.llm_description and _SEPARATOR in post.llm_description:
        what, so_what = _split_description(post.llm_description)
        return {"description": what, "so_what": so_what, "cached": True}
    # Clear malformed cache (old format or WHAT: prefix leaked in)
    if post.llm_description and post.llm_description.upper().startswith("WHAT:"):
        post.llm_description = None
        await db.commit()

    from app.services.llm_router import call_llm_async

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

    try:
        reply = await call_llm_async(messages, max_tokens=8192)
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


# ── Tracked accounts registry ─────────────────────────────
# The client asked to "define and track specific social media accounts".
# Keyword search pays for N results and hopes; a chosen account is on-topic by
# construction, which is why this is the highest-yield lever on French volume.

class TrackedAccountIn(BaseModel):
    platform: str
    handle: str
    url: str | None = None
    label: str | None = None
    category: str | None = None
    active: bool = True


def _account_out(a) -> dict:
    return {
        "id": a.id, "platform": a.platform, "handle": a.handle, "url": a.url,
        "label": a.label, "category": a.category, "active": a.active,
    }


async def _account_coverage(db) -> dict[tuple[str, str], dict]:
    """Posts collected per tracked account, so a dead account is visible.

    Tracking an account is only worth anything if posts actually arrive from it,
    and a wrong handle fails silently: seeding LinkedIn turned up `gustaveroussy`,
    which returns ten results none of which are Gustave Roussy — the real slug is
    `gustave-roussy`. Without a count per account that stays invisible forever.

    Aggregated in SQL rather than in Python. The obvious version pulls every post
    and loops the accounts across it, which is 51 x N comparisons on every page
    load of the Social tab — fine on a dev table, quadratic in production.

    Two attribution routes, because the lanes label differently: the account
    lanes stamp `query = 'tracked:<platform>:<handle>'`, and every normaliser
    fills `author`. Both group cleanly; a URL-substring fallback deliberately is
    not used, since matching "roche" inside a URL also matches "rochester".
    """
    from app.models import TrackedAccount

    accounts = (await db.execute(select(TrackedAccount))).scalars().all()
    if not accounts:
        return {}

    # Author, normalised the same way the handle is (case-folded, no leading @).
    author_key = func.lower(func.ltrim(func.trim(SocialPost.author), "@"))
    by_author = await db.execute(
        select(SocialPost.platform, author_key,
               func.count(), func.max(SocialPost.scraped_at))
        .where(social_not_ae(), SocialPost.author.is_not(None))
        .group_by(SocialPost.platform, author_key)
    )
    by_query = await db.execute(
        select(SocialPost.platform, func.lower(SocialPost.query),
               func.count(), func.max(SocialPost.scraped_at))
        .where(social_not_ae(), SocialPost.query.like("tracked:%"))
        .group_by(SocialPost.platform, func.lower(SocialPost.query))
    )

    author_hits: dict[tuple[str, str], tuple[int, object]] = {
        (platform, key): (count, last)
        for platform, key, count, last in by_author.all() if key
    }
    query_hits: dict[str, tuple[int, object]] = {
        key: (count, last) for _, key, count, last in by_query.all() if key
    }

    coverage: dict[tuple[str, str], dict] = {}
    for account in accounts:
        handle = (account.handle or "").strip().lstrip("@").lower()
        if not handle:
            continue
        count, last = author_hits.get((account.platform, handle), (0, None))
        tag_count, tag_last = query_hits.get(
            f"tracked:{account.platform}:{handle}", (0, None))
        # The same post can carry both the tag and the author, so take the larger
        # rather than the sum — adding them would double-count the account lanes.
        if tag_count > count:
            count, last = tag_count, tag_last
        elif tag_last and (last is None or tag_last > last):
            last = tag_last
        coverage[(account.platform, handle)] = {
            "post_count": count,
            "last_seen": last.isoformat() if last else None,
        }
    return coverage


@router.get("/accounts")
async def list_accounts(db: AsyncSession = Depends(get_db)):
    from app.models import TrackedAccount

    rows = await db.execute(
        select(TrackedAccount).order_by(TrackedAccount.platform, TrackedAccount.handle)
    )
    accounts = rows.scalars().all()
    coverage = await _account_coverage(db)
    out = []
    for a in accounts:
        item = _account_out(a)
        stats = coverage.get((a.platform, (a.handle or "").strip().lstrip("@").lower()),
                             {"post_count": 0, "last_seen": None})
        out.append({**item, **stats})
    return {"accounts": out}


@router.post("/accounts", status_code=201, dependencies=[Depends(require_admin)])
async def create_account(body: TrackedAccountIn, db: AsyncSession = Depends(get_db)):
    from app.models import TrackedAccount
    from app.models.tracked_account import PLATFORMS

    platform = (body.platform or "").strip().lower()
    if platform not in PLATFORMS:
        raise HTTPException(status_code=422, detail=f"platform must be one of {', '.join(PLATFORMS)}")
    handle = (body.handle or "").strip().lstrip("@")
    if not handle:
        raise HTTPException(status_code=422, detail="handle is required")

    existing = await db.execute(
        select(TrackedAccount).where(
            TrackedAccount.platform == platform,
            func.lower(TrackedAccount.handle) == handle.lower(),
        )
    )
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail="That account is already tracked")

    row = TrackedAccount(
        platform=platform, handle=handle,
        url=(body.url or "").strip() or (f"https://x.com/{handle}" if platform == "twitter" else None),
        label=(body.label or "").strip() or None,
        category=(body.category or "").strip() or None,
        active=body.active,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _account_out(row)


@router.patch("/accounts/{account_id}", dependencies=[Depends(require_admin)])
async def update_account(account_id: int, body: dict, db: AsyncSession = Depends(get_db)):
    from app.models import TrackedAccount

    row = await db.get(TrackedAccount, account_id)
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
    for field in ("label", "category", "url"):
        if field in body:
            setattr(row, field, (body[field] or "").strip() or None)
    if "active" in body:
        row.active = bool(body["active"])
    await db.commit()
    await db.refresh(row)
    return _account_out(row)


@router.delete("/accounts/{account_id}", dependencies=[Depends(require_admin)])
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    from app.models import TrackedAccount

    row = await db.get(TrackedAccount, account_id)
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
    await db.delete(row)
    await db.commit()
    return {"deleted": account_id}


# ── Single-post analysis ──────────────────────────────────

def _post_reach(post, platform_stats: dict) -> dict:
    """This post's pull, benchmarked against its own platform.

    "Volume of mentions" is meaningless for one post — it is always one. What is
    useful is how far THIS post travelled relative to others on the same
    platform, so that is what the section reports.

    X and LinkedIn arrive through search, which carries no engagement at all, so
    their numbers are absent rather than zero. Rendering 0 there would read as
    "nobody engaged" when the truth is "we cannot see engagement".
    """
    engagement = (post.likes or 0) + (post.comments or 0) + (post.views or 0)
    available = post.platform in ("instagram", "facebook")
    stats = platform_stats.get(post.platform) or {}
    average = stats.get("avg") or 0
    return {
        "available": available,
        "likes": post.likes or 0,
        "comments": post.comments or 0,
        "views": post.views or 0,
        "engagement": engagement,
        "platform_average": round(average, 1) if available else None,
        "vs_average": (round(engagement / average, 1)
                       if available and average else None),
        "platform": post.platform,
        "note": (None if available else
                 f"{post.platform} posts are collected through search, which "
                 "does not expose engagement figures."),
    }


@router.post("/post/{post_id}/analyse")
async def analyse_post(post_id: int, refresh: bool = False,
                       db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """Analyse one post on its own, in the report format used everywhere else."""
    post = await db.get(SocialPost, post_id)
    if not post or post.is_adverse_event is True:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.analysis_sections and not refresh:
        try:
            return {"sections": json.loads(post.analysis_sections), "cached": True}
        except Exception:                           # noqa: BLE001 - rewrite bad cache
            pass

    if not (post.text or "").strip():
        raise HTTPException(422, "This post has no text to analyse")

    rows = await db.execute(
        select(SocialPost.platform,
               func.avg(func.coalesce(SocialPost.likes, 0)
                        + func.coalesce(SocialPost.comments, 0)
                        + func.coalesce(SocialPost.views, 0)))
        .group_by(SocialPost.platform)
    )
    platform_stats = {p: {"avg": float(a or 0)} for p, a in rows.all()}

    # Who is speaking is a classification, not a distribution: one post has one
    # author, so a percentage chart would be theatre.
    from app.services.voice_profile import classify
    bucket, confidence, evidence = classify(
        post.author or "", url=post.post_url or "",
        is_tracked_kol=False, target_type=None)

    hashtags = json.loads(post.hashtags) if post.hashtags else []
    reach = _post_reach(post, platform_stats)

    prompt = (
        "You are a pharma intelligence analyst monitoring the French market for "
        "Roche. Analyse this SINGLE social media post.\n\n"
        "Output EXACTLY these sections, each starting with its marker:\n\n"
        "##EXEC_SUMMARY##\n2-3 sentences: what this post is, who published it "
        "and the context.\n\n"
        "##SO_WHAT##\n2-3 sentences on the strategic implication for a pharma "
        "medical affairs team — the signal, opportunity or risk, and what to do "
        "about it. Be specific.\n\n"
        "##WHAT_IS_SAID##\n2-4 sentences on the substance: the actual claims, "
        "products, studies or positions stated in the post.\n\n"
        "##VOICE##\n1-2 sentences on who is speaking and what standing they "
        f"have. Our classifier reads this author as: {bucket} ({confidence}). "
        "Say whether the post supports that reading.\n\n"
        "##REACH##\n1-2 sentences interpreting how far this post travelled, "
        f"given: {json.dumps(reach)}. If engagement is unavailable on this "
        "platform, say so plainly and do not guess.\n\n"
        "##SUBTOPICS##\n3-5 lines starting '- ': the themes this post touches "
        "that are worth tracking.\n\n"
        "Base every statement on the post. Only the post text is available — "
        "comment text is NOT collected, so never state what commenters said.\n\n"
        f"Platform: {post.platform}\nAuthor: {post.author or 'unknown'}\n"
        f"Posted: {post.posted_at.date().isoformat() if post.posted_at else 'undated'}\n"
        f"Hashtags: {', '.join(hashtags) if hashtags else '-'}\n"
        f"Engagement: {reach['likes']} likes, {reach['comments']} comments, "
        f"{reach['views']} views\n\n"
        f"Post text:\n{(post.text or '')[:4000]}"
    )

    from app.services.llm_router import call_llm_async
    try:
        # gemini-2.5-flash counts reasoning against this budget, so a small cap
        # silently truncates the tail sections.
        reply = await call_llm_async([{"role": "user", "content": prompt}], max_tokens=6000)
    except Exception as exc:                        # noqa: BLE001
        raise HTTPException(502, f"LLM call failed: {str(exc)[:200]}") from exc

    from app.services.synthesizer import extract_section, parse_bullets, trim_incomplete

    raw = reply or ""
    sections = {
        "exec_summary": trim_incomplete(extract_section(raw, "EXEC_SUMMARY")),
        "so_what": trim_incomplete(extract_section(raw, "SO_WHAT")),
        "what_is_said": trim_incomplete(extract_section(raw, "WHAT_IS_SAID")),
        "voice_note": trim_incomplete(extract_section(raw, "VOICE")),
        "reach_note": trim_incomplete(extract_section(raw, "REACH")),
        "subtopics": parse_bullets(extract_section(raw, "SUBTOPICS")),
        # Computed, not written by the model.
        "voice": {"bucket": bucket, "confidence": confidence, "evidence": evidence},
        "reach": reach,
    }
    post.analysis_sections = json.dumps(sections)
    post.analysed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"sections": sections, "cached": False}


@router.get("/post/{post_id}/analysis")
async def get_post_analysis(post_id: int, db: AsyncSession = Depends(get_db),
                            user: User = Depends(get_current_user)):
    """Whatever analysis is already stored. Never calls the LLM.

    Opening a post must not spend money — generating is an explicit action.
    """
    post = await db.get(SocialPost, post_id)
    if not post or post.is_adverse_event is True:
        raise HTTPException(status_code=404, detail="Post not found")
    if not post.analysis_sections:
        return {"sections": None, "cached": False}
    try:
        return {"sections": json.loads(post.analysis_sections), "cached": True}
    except Exception:                               # noqa: BLE001
        return {"sections": None, "cached": False}
