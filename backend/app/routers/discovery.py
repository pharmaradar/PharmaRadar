"""Discovery — real-time TinyFish search + DB cache."""
import asyncio
import hashlib
import re
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, desc, update
from sqlalchemy.ext.asyncio import AsyncSession

# Max concurrent TinyFish subprocess calls across all discovery endpoints.
# Each spawns a headless browser (~400MB). 8 = ~3.2GB peak on backend service.
_DISCOVERY_SEM = asyncio.Semaphore(8)

from app.database import get_db
from app.models.discovery_result import DiscoveryResult
from app.models import SearchHistory, User
from app.auth import get_current_user, enforce_daily_generation

router = APIRouter(prefix="/api/discovery", tags=["discovery"])

MIN_RESULTS = 10
MAX_RESULTS = 20
DEEP_MAX_RESULTS = 80
FETCH_TIMEOUT = 12

# Only block raw search engine result pages — allow everything else
_SKIP_DOMAINS = {
    "google.com", "bing.com", "duckduckgo.com", "yahoo.com",
    "wikidata.org",  # metadata only, no content
}

# Social / media type detection
_SOCIAL_DOMAINS = {
    "linkedin.com": "linkedin",
    "twitter.com": "twitter",
    "x.com": "twitter",
    "youtube.com": "video",
    "youtu.be": "video",
    "instagram.com": "social",
    "facebook.com": "social",
    "linkedin.com": "social",
    "reddit.com": "social",
    "researchgate.net": "research",
    "academia.edu": "research",
    "pubmed.ncbi.nlm.nih.gov": "research",
    "pmc.ncbi.nlm.nih.gov": "research",
    "sciencedirect.com": "research",
    "springer.com": "research",
    "nature.com": "research",
    "nejm.org": "research",
    "thelancet.com": "research",
}


# ── Media type detection ──────────────────────────────────

def _youtube_id(url: str) -> str | None:
    for pattern in [r"youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})", r"youtu\.be/([a-zA-Z0-9_-]{11})"]:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def _detect_media_type(url: str) -> tuple[str, str | None]:
    """Returns (media_type, thumbnail_url)."""
    vid = _youtube_id(url)
    if vid:
        return "video", f"https://img.youtube.com/vi/{vid}/hqdefault.jpg"
    if url.lower().endswith(".pdf"):
        return "pdf", None
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower().replace("www.", "")
        for sd, st in _SOCIAL_DOMAINS.items():
            if domain == sd or domain.endswith("." + sd):
                return st, None
    except Exception:
        pass
    return "article", None


def _parse_date_for_sort(date_str: str | None, fallback: str = "") -> str:
    """Normalize date string to ISO format for sorting."""
    if not date_str:
        return fallback
    import re as _re
    # Already ISO
    if _re.match(r"^\d{4}-\d{2}-\d{2}", date_str):
        return date_str
    # DD/MM/YYYY or DD-MM-YYYY
    m = _re.match(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", date_str)
    if m:
        return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
    # Month name formats — just return as-is for now
    return date_str


def _is_blocked(content: str) -> bool:
    blocked_phrases = [
        "checking your browser", "ddos protection", "cloudflare",
        "please wait while we verify", "are you human",
        "click here if you are not automatically redirected",
        "enable javascript", "access denied",
    ]
    lower = content.lower()[:500]
    return any(p in lower for p in blocked_phrases)


# ── Content cleaning ─────────────────────────────────────

def _clean_content(text: str) -> str:
    """Convert raw scraped text into clean readable prose."""
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        # Strip leading markdown symbols
        line = re.sub(r"^#+\s*", "", line)      # ## headers
        line = re.sub(r"^\*+\s*", "", line)     # * bullets
        line = re.sub(r"^-+\s*", "", line)      # - bullets
        line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)  # **bold**
        line = re.sub(r"\*(.*?)\*", r"\1", line)       # *italic*
        line = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", line) # [text](url)
        line = line.strip()

        # Skip noise lines
        if not line or len(line) < 15:
            continue
        if re.match(r"^(Previous Close|Open|Bid|Ask|Volume|Market Cap|PE Ratio|EPS|Beta|52 Week)", line):
            continue
        if re.match(r"^\d+\.\d+\s*[\+\-]", line):  # stock prices
            continue
        if re.match(r"^Q[1-4]\s+FY\d+|^(Revenue|Earnings)\s+[\d\.]+[BMK]", line):
            continue

        cleaned.append(line)

    # Deduplicate consecutive identical lines
    deduped = []
    prev = None
    for line in cleaned:
        if line != prev:
            deduped.append(line)
        prev = line

    return "\n".join(deduped)


# ── Helpers ───────────────────────────────────────────────

def _sha256(text: str) -> str:
    normalised = re.sub(r"\s+", " ", (text or "")).strip().lower()
    return hashlib.sha256(normalised.encode()).hexdigest()


def _extract_date(snippet: str) -> str | None:
    """Try to extract a published date from snippet text."""
    patterns = [
        r'\b(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})\b',
        r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}\b',
        r'\b\d{4}[-/]\d{2}[-/]\d{2}\b',
        r'\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})\b',
    ]
    for p in patterns:
        m = re.search(p, snippet or "", re.IGNORECASE)
        if m:
            return m.group(0)
    return None


def _extract_source_name(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def _is_skipped_domain(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower().replace("www.", "")
        return any(domain == d or domain.endswith("." + d) for d in _SKIP_DOMAINS)
    except Exception:
        return False


def _extract_og_image(html: str) -> str | None:
    """Extract og:image or twitter:image from page content."""
    import re as _re
    patterns = [
        r'og:image["\s]+content=["\']([^"\']+)["\']',
        r'content=["\']([^"\']+)["\']["\s]+og:image',
        r'twitter:image["\s]+content=["\']([^"\']+)["\']',
        r'content=["\']([^"\']+)["\']["\s]+twitter:image',
    ]
    for p in patterns:
        m = _re.search(p, html[:10000], _re.IGNORECASE)
        if m:
            url = m.group(1).strip()
            if url.startswith("http") and any(ext in url.lower() for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"]):
                return url
            elif url.startswith("http"):
                return url
    return None


def _detect_lang(text: str) -> str:
    if not text or len(text) < 20:
        return "en"
    lower = text.lower()[:1000]
    fr_signals = [" de ", " du ", " le ", " la ", " les ", " des ", " et ",
                  " en ", " une ", " pour ", " avec ", " dans ", " sur ",
                  " est ", " sont ", " nous ", " vous ", " ils ", " au ",
                  "qu'", " l'", " d'", " n'"]
    return "fr" if sum(1 for w in fr_signals if w in lower) >= 4 else "en"


def _to_out(r: DiscoveryResult, from_cache: bool) -> dict:
    return {
        "id": r.id,
        "query": r.query,
        "url": r.url,
        "title": r.title,
        "snippet": r.snippet,
        "content": r.content,
        "source_name": r.source_name,
        "published_date": r.published_date,
        "scraped_at": r.scraped_at.isoformat() if r.scraped_at else "",
        "from_cache": from_cache,
        "media_type": r.media_type or "article",
        "thumbnail_url": r.thumbnail_url,
        "language": r.language or "en",
        "llm_description": r.llm_description,
    }


# ── Endpoints ─────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    force_refresh: bool = False
    lang: str | None = None   # "fr" | "en" | "all" — None = use default (France)


class FetchRequest(BaseModel):
    result_id: int
    url: str


def _localize(q: str, lang: str | None) -> str:
    """Append geographic/language hints to a search query."""
    if not lang or lang == "all":
        return q
    if lang == "fr":
        # French TLD restriction + French-language keyword bias
        return f"{q} (site:.fr OR France OR français)"
    if lang == "en":
        return f"{q} -site:.fr -site:.de -site:.it -site:.es"
    return q


def _variant_queries(query: str, lang: str | None = "fr") -> list[str]:
    """Standard search variants — 5 queries for regular search."""
    q = query.strip()
    base = [
        q,
        f"{q} site:linkedin.com",
        f"{q} site:twitter.com OR site:x.com",
        f"{q} 2025 2024 news",
        f"{q} clinical trial research study",
    ]
    return [_localize(v, lang) for v in base]


def _deep_queries(query: str, lang: str | None = "fr") -> list[str]:
    """Comprehensive query list for deep search — covers all platforms and angles."""
    q = query.strip()
    base = [
        q,
        f"{q} 2025",
        f"{q} 2024",
        f"{q} 2023",
        f"{q} site:linkedin.com",
        f"{q} site:twitter.com",
        f"{q} site:youtube.com",
        f"{q} site:pubmed.ncbi.nlm.nih.gov",
        f"{q} site:researchgate.net",
        f"{q} news article",
        f"{q} clinical trial results",
        f"{q} conference presentation ASCO ESMO",
        f"{q} expert opinion KOL",
        f"{q} latest update",
        f"{q} discussion forum",
    ]
    return [_localize(v, lang) for v in base]


async def _save_hit(db, query: str, hit: dict, seen_urls: set) -> dict | None:
    url = hit.get("url", "")
    if not url or _is_skipped_domain(url) or url in seen_urls:
        return None
    seen_urls.add(url)

    snippet = _clean_content(hit.get("snippet", ""))
    media_type, thumbnail_url = _detect_media_type(url)
    pub_date = _extract_date(hit.get("snippet", ""))
    ch = _sha256(url)

    existing_row = await db.execute(
        select(DiscoveryResult).where(DiscoveryResult.query == query, DiscoveryResult.url == url)
    )
    if existing_row.scalar_one_or_none():
        return None

    row = DiscoveryResult(
        query=query, url=url,
        title=hit.get("title") or None,
        snippet=snippet, content=None,
        source_name=_extract_source_name(url),
        published_date=pub_date,
        content_hash=ch,
        media_type=media_type,
        thumbnail_url=thumbnail_url,
        language=_detect_lang((hit.get("title") or "") + " " + (hit.get("snippet") or "")),
    )
    db.add(row)
    try:
        await db.flush()
        return _to_out(row, from_cache=False)
    except Exception:
        await db.rollback()
        return None


@router.post("/search")
async def search(body: SearchRequest, db: AsyncSession = Depends(get_db),
                 user: User = Depends(get_current_user)):
    query = body.query.strip()
    if not query:
        return {"results": [], "from_cache": False, "count": 0}

    # Record this user's search (cache stays shared; this is just per-user history)
    db.add(SearchHistory(user_id=user.id, kind="discovery", query=query))
    await db.commit()

    # Check DB cache first
    if not body.force_refresh:
        existing = await db.execute(
            select(DiscoveryResult)
            .where(DiscoveryResult.query == query)
            .order_by(desc(DiscoveryResult.scraped_at))
            .limit(MAX_RESULTS)
        )
        cached = [r for r in existing.scalars().all() if not _is_skipped_domain(r.url)]
        if len(cached) >= MIN_RESULTS:
            return {"results": [_to_out(r, True) for r in cached], "from_cache": True, "count": len(cached)}

    # Set discovery:active flag
    try:
        import redis as _redis
        from app.config import get_settings as _gs
        from app.services.scraper import DISCOVERY_ACTIVE_REDIS_KEY
        _redis.Redis.from_url(_gs().redis_url, socket_timeout=1).set(DISCOVERY_ACTIVE_REDIS_KEY, "1", ex=90)
    except Exception:
        pass

    from app.services.scraper import _tf_search_discovery

    results: list[dict] = []
    seen_urls: set = set()
    queries = _variant_queries(query, body.lang or "fr")
    loop = asyncio.get_event_loop()

    # Run primary query first, then variants until we hit MAX_RESULTS
    for vq in queries:
        if len(results) >= MAX_RESULTS:
            break
        needed = MAX_RESULTS - len(results)
        async with _DISCOVERY_SEM:
            hits = await loop.run_in_executor(None, _tf_search_discovery, vq)
        hits = hits[:needed + 5]
        for hit in hits:
            if len(results) >= MAX_RESULTS:
                break
            saved = await _save_hit(db, query, hit, seen_urls)
            if saved:
                results.append(saved)

    await db.commit()

    # Fallback: if still under MIN, return whatever we have from cache too
    if len(results) < MIN_RESULTS:
        existing = await db.execute(
            select(DiscoveryResult)
            .where(DiscoveryResult.query == query)
            .order_by(desc(DiscoveryResult.scraped_at))
            .limit(MAX_RESULTS)
        )
        cached_all = [r for r in existing.scalars().all() if not _is_skipped_domain(r.url)]
        cached_ids = {r["id"] for r in results}
        for r in cached_all:
            if r.id not in cached_ids and len(results) < MAX_RESULTS:
                results.append(_to_out(r, True))

    return {"results": results[:MAX_RESULTS], "from_cache": False, "count": len(results)}


@router.post("/fetch-content")
async def fetch_content(body: FetchRequest, db: AsyncSession = Depends(get_db)):
    row = await db.get(DiscoveryResult, body.result_id)

    # YouTube: no content needed, return embed info
    vid = _youtube_id(body.url)
    if vid:
        return {"content": None, "media_type": "video", "youtube_id": vid, "blocked": False}

    # Already fetched
    if row and row.content:
        return {"content": row.content, "media_type": row.media_type or "article", "blocked": False}

    # Fetch via TinyFish with short timeout
    try:
        from app.services.scraper import _tf_fetch_discovery
        import asyncio
        loop = asyncio.get_event_loop()
        raw = await asyncio.wait_for(
            loop.run_in_executor(None, _tf_fetch_discovery, body.url),
            timeout=FETCH_TIMEOUT
        )
    except (asyncio.TimeoutError, Exception):
        raw = None

    if not raw:
        return {"content": None, "media_type": "article", "blocked": False, "error": "timeout"}

    if _is_blocked(raw):
        return {"content": None, "media_type": "article", "blocked": True}

    content = _clean_content(raw)[:5000]

    # Try extract OG image from raw HTML
    og_image = _extract_og_image(raw)

    updates: dict = {}
    if content:
        updates["content"] = content
    if og_image and row and not row.thumbnail_url:
        updates["thumbnail_url"] = og_image

    if updates and row:
        await db.execute(
            update(DiscoveryResult).where(DiscoveryResult.id == body.result_id).values(**updates)
        )
        await db.commit()

    media_type = row.media_type if row else "article"
    return {"content": content or None, "media_type": media_type, "blocked": False, "thumbnail_url": og_image}


@router.post("/deep-search")
async def deep_search(body: SearchRequest, db: AsyncSession = Depends(get_db)):
    """Comprehensive deep search — runs 15 query variants, returns up to 80 unique results
    sorted newest to oldest."""
    query = body.query.strip()
    if not query:
        return {"results": [], "count": 0}

    try:
        import redis as _redis
        from app.config import get_settings as _gs
        from app.services.scraper import DISCOVERY_ACTIVE_REDIS_KEY
        _redis.Redis.from_url(_gs().redis_url, socket_timeout=1).set(DISCOVERY_ACTIVE_REDIS_KEY, "1", ex=300)
    except Exception:
        pass

    from app.services.scraper import _tf_search_discovery

    all_results: list[dict] = []
    seen_urls: set = set()
    deep_key = f"__deep__{query}"  # separate cache key for deep results
    loop = asyncio.get_event_loop()

    for vq in _deep_queries(query, body.lang or "fr"):
        if len(all_results) >= DEEP_MAX_RESULTS:
            break
        async with _DISCOVERY_SEM:
            hits = await loop.run_in_executor(None, _tf_search_discovery, vq)
        hits = hits[:12]
        for hit in hits:
            if len(all_results) >= DEEP_MAX_RESULTS:
                break
            saved = await _save_hit(db, deep_key, hit, seen_urls)
            if saved:
                all_results.append(saved)

    await db.commit()

    # Sort by date descending
    def _sort_key(r: dict) -> str:
        d = r.get("published_date") or r.get("scraped_at") or ""
        return _parse_date_for_sort(d, d)

    all_results.sort(key=_sort_key, reverse=True)

    return {"results": all_results, "count": len(all_results)}


@router.get("/history")
async def history(db: AsyncSession = Depends(get_db),
                  user: User = Depends(get_current_user)):
    rows = await db.execute(
        select(SearchHistory.query, SearchHistory.created_at)
        .where(SearchHistory.kind == "discovery", SearchHistory.user_id == user.id)
        .order_by(desc(SearchHistory.created_at))
        .limit(200)
    )
    seen: set = set()
    queries = []
    for q, at in rows:
        if q not in seen:
            seen.add(q)
            queries.append({"query": q, "scraped_at": at.isoformat()})
    return {"queries": queries[:20]}


@router.get("/kol-mentions")
async def kol_mentions(q: str, db: AsyncSession = Depends(get_db)):
    """Search existing extracted insights for mentions of a topic.
    Returns flat list of insights with KOL name + date, sorted newest first.
    Split into recent (≤180 days) and historical (>180 days).
    """
    from app.models import ExtractedInsight, Target, ScrapedPost
    from sqlalchemy import or_, func
    from datetime import datetime, timezone, timedelta

    if not q or len(q.strip()) < 2:
        return {"recent": [], "historical": [], "total": 0}

    term = q.strip().lower()
    cutoff = datetime.now(timezone.utc) - timedelta(days=180)

    rows = await db.execute(
        select(ExtractedInsight)
        .where(or_(
            func.lower(ExtractedInsight.topic).contains(term),
            func.lower(ExtractedInsight.what_they_said).contains(term),
            func.lower(ExtractedInsight.context).contains(term),
        ))
        .order_by(desc(ExtractedInsight.extracted_at))
        .limit(300)
    )
    insights = rows.scalars().all()

    if not insights:
        return {"recent": [], "historical": [], "total": 0}

    # Fetch target names
    target_ids = {ins.target_id for ins in insights}
    post_ids = {ins.scraped_post_id for ins in insights if ins.scraped_post_id}

    targets_rows = await db.execute(select(Target).where(Target.id.in_(list(target_ids))))
    targets = {t.id: t.name for t in targets_rows.scalars().all()}

    # Fetch post published dates
    pub_dates: dict = {}
    if post_ids:
        posts_rows = await db.execute(
            select(ScrapedPost.id, ScrapedPost.published_date, ScrapedPost.source_url, ScrapedPost.source_name)
            .where(ScrapedPost.id.in_(list(post_ids)))
        )
        for pid, pdate, purl, psource in posts_rows:
            pub_dates[pid] = {"date": pdate, "url": purl, "source": psource}

    def _make(ins: ExtractedInsight) -> dict:
        post_info = pub_dates.get(ins.scraped_post_id, {})
        date_str = post_info.get("date") or (ins.extracted_at.strftime("%Y-%m-%d") if ins.extracted_at else "")
        return {
            "id": ins.id,
            "kol": targets.get(ins.target_id, f"KOL {ins.target_id}"),
            "topic": ins.topic,
            "what_they_said": ins.what_they_said,
            "sentiment": ins.sentiment,
            "category": ins.category,
            "published_date": date_str,
            "source_url": post_info.get("url"),
            "source_name": post_info.get("source"),
            "extracted_at": ins.extracted_at.isoformat() if ins.extracted_at else "",
        }

    recent = []
    historical = []
    for ins in insights:
        item = _make(ins)
        if ins.extracted_at and ins.extracted_at >= cutoff:
            recent.append(item)
        else:
            historical.append(item)

    # Sort each by published_date desc
    def sort_key(x: dict):
        return x.get("published_date") or x.get("extracted_at") or ""

    recent.sort(key=sort_key, reverse=True)
    historical.sort(key=sort_key, reverse=True)

    return {"recent": recent[:50], "historical": historical[:50], "total": len(insights)}


# ── Synthesis / takeaway + LLM editor's picks ────────────

class SynthesisRequest(BaseModel):
    query: str
    lang: str | None = None
    refresh: bool = False


@router.post("/synthesis")
async def synthesis(body: SynthesisRequest, db: AsyncSession = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """On-demand LLM synthesis of everything found for a query — web/social
    results + KOL mentions. Returns a takeaway, 'so what for Roche', and the
    LLM's pick of the most interesting/impactful results (each with a why).
    Cached 6h in Redis keyed by query+lang.
    """
    import json as _json
    from app.config import get_settings
    # One forced fresh synthesis per user per day (admins unlimited)
    if body.refresh:
        enforce_daily_generation(user, "discovery_synthesis")
    from app.services.synthesizer import parse_synthesis
    from app.models import ExtractedInsight
    from sqlalchemy import or_, func

    query = (body.query or "").strip()
    if len(query) < 2:
        return {"takeaway": "", "so_what": "", "highlights": [], "total": 0,
                "generated_at": None, "cached": False, "error": None}

    qhash = hashlib.sha256(f"{query.lower()}|{body.lang or 'all'}".encode()).hexdigest()[:16]
    key = f"disc_synth:v1:{qhash}"
    ukey = f"{key}:u{user.id}"   # private regenerate per user; shared key untouched
    r = None
    try:
        import redis as _redis
        r = _redis.Redis.from_url(get_settings().redis_url, socket_timeout=2)
        if not body.refresh:
            cached = r.get(ukey) or r.get(key)
            if cached:
                return _json.loads(cached)
    except Exception:
        r = None

    # Web / social results for this query
    res_rows = await db.execute(
        select(DiscoveryResult)
        .where(DiscoveryResult.query == query)
        .order_by(desc(DiscoveryResult.scraped_at))
        .limit(60)
    )
    results = res_rows.scalars().all()
    if body.lang and body.lang != "all":
        results = [r0 for r0 in results if (r0.language or "en") == body.lang]

    # KOL mentions (context only — not pickable)
    term = query.lower()
    kol_rows = await db.execute(
        select(ExtractedInsight)
        .where(or_(
            func.lower(ExtractedInsight.topic).contains(term),
            func.lower(ExtractedInsight.what_they_said).contains(term),
        ))
        .order_by(desc(ExtractedInsight.extracted_at))
        .limit(20)
    )
    kol_insights = kol_rows.scalars().all()

    if not results and not kol_insights:
        return {"takeaway": "", "so_what": "", "highlights": [], "total": 0,
                "generated_at": None, "cached": False, "error": None}

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    listing = "\n".join(
        f"[{r0.id}] ({r0.media_type or 'article'}, {r0.source_name or _extract_source_name(r0.url)}) "
        f"\"{(r0.snippet or r0.title or '')[:200]}\""
        for r0 in results[:40]
    )
    kol_block = "\n".join(
        f"- {ins.topic}: \"{(ins.what_they_said or '')[:160]}\" ({ins.sentiment})"
        for ins in kol_insights[:15]
    ) or "(none)"

    prompt = (
        "You are a senior pharma intelligence analyst for Roche France.\n"
        f"A user searched the topic: \"{query}\".\n\n"
        f"WEB & SOCIAL RESULTS (each prefixed with its [id]):\n{listing or '(none)'}\n\n"
        f"WHAT YOUR MONITORED KOLs SAID ABOUT THIS TOPIC:\n{kol_block}\n\n"
        "Write a concise intelligence synthesis. Use EXACTLY this format with these markers:\n"
        "##TAKEAWAY##\n"
        "3-5 sentences: the state of play on this topic across the web, social and your KOLs.\n"
        "##SO_WHAT##\n"
        "2-3 sentences on what this means for Roche France and what to do next.\n"
        "##CONCLUSION##\n"
        "2-3 sentences: the bottom line — the single most important thing to focus on now.\n"
        "##PICKS##\n"
        "The 4-6 most interesting / impactful results. One per line, format: [id] one-sentence why it matters. "
        "Use the real [id] values from the WEB & SOCIAL RESULTS list above.\n\n"
        "Reference real drug names, sources and findings. Be specific."
    )

    from app.services.llm_router import call_pro
    err = None
    parsed = {"takeaway": "", "so_what": "", "picks": []}
    try:
        raw = call_pro([{"role": "user", "content": prompt}], max_tokens=2500)
        parsed = parse_synthesis(raw)
    except Exception as exc:
        err = str(exc)[:300]

    by_id = {r0.id: r0 for r0 in results}
    highlights = []
    for pick in parsed["picks"][:6]:
        r0 = by_id.get(pick["id"])
        if r0:
            out = _to_out(r0, from_cache=True)
            out["why"] = pick["why"]
            highlights.append(out)

    result = {
        "takeaway": parsed["takeaway"],
        "so_what": parsed["so_what"],
        "conclusion": parsed["conclusion"],
        "highlights": highlights,
        "total": len(results) + len(kol_insights),
        "generated_at": now.isoformat(),
        "cached": False,
        "error": err,
    }
    try:
        if r and (parsed["takeaway"] or highlights):
            r.set(ukey if body.refresh else key, _json.dumps(result), ex=21600)
    except Exception:
        pass
    return result


# ── Describe endpoint (LLM, cached per result) ───────────

class DescribeDiscoveryRequest(BaseModel):
    result_id: int


@router.post("/describe")
async def describe_discovery(body: DescribeDiscoveryRequest, db: AsyncSession = Depends(get_db)):
    """Generate LLM description + pharma so-what for a discovery result. Cached on the row."""
    row = await db.get(DiscoveryResult, body.result_id)
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Result not found")

    if row.llm_description:
        parts = row.llm_description.split("\n\n@@SO_WHAT@@\n\n", 1)
        return {"description": parts[0], "so_what": parts[1] if len(parts) > 1 else None, "cached": True}

    text = " ".join(filter(None, [row.title, row.snippet, (row.content or "")[:3000]]))
    if not text.strip():
        return {"description": "No content available.", "so_what": None, "cached": False}

    from app.services.llm_router import call_pro
    prompt = (
        "You are a pharma intelligence analyst for Roche.\n"
        f"Source: {row.source_name or row.url}\n\n"
        f"Content:\n{text[:4000]}\n\n"
        "Reply with JSON: {\"description\": \"2-3 sentence summary of what this is\", "
        "\"so_what\": \"1-2 sentence takeaway specifically relevant to Roche and oncology\"}"
    )
    try:
        raw = call_pro([{"role": "user", "content": prompt}], max_tokens=512)
        import json as _json, re as _re
        m = _re.search(r'\{.*\}', raw, _re.DOTALL)
        parsed = _json.loads(m.group(0)) if m else {}
        description = parsed.get("description", raw[:400])
        so_what = parsed.get("so_what")
    except Exception:
        description = text[:400]
        so_what = None

    row.llm_description = description + ("\n\n@@SO_WHAT@@\n\n" + so_what if so_what else "")
    await db.commit()
    return {"description": description, "so_what": so_what, "cached": False}
