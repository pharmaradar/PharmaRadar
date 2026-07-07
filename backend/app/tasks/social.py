"""Social trend scan — manual-trigger Apify scrape across platforms.

Reads the curated config from AppSettings (medical/drug/treatment keywords +
optionally KOL names), runs the per-platform Apify Actors, and ingests the
results into the social_posts table with real engagement counts. No LLM runs
during the scan — topic descriptions are generated on demand when the user
clicks a trend (see routers/social.py).
"""
import asyncio
import json


def _detect_lang(text: str) -> str:
    """Lightweight FR/EN detector based on French function-word frequency."""
    if not text or len(text) < 20:
        return "en"
    lower = text.lower()[:1000]
    fr_signals = [" de ", " du ", " le ", " la ", " les ", " des ", " et ",
                  " en ", " une ", " pour ", " avec ", " dans ", " sur ",
                  " est ", " sont ", " nous ", " vous ", " ils ", " au ",
                  "qu'", " l'", " d'", " n'"]
    return "fr" if sum(1 for w in fr_signals if w in lower) >= 4 else "en"

import structlog

# ── Pharma relevance gate ─────────────────────────────────
# Posts are only stored if they contain at least one of these signals in
# their text, hashtags, or topic. This filters out off-topic results that
# happen to mention a vague keyword (e.g. "drug" = illegal drugs, slang).
_PHARMA_SIGNALS = frozenset({
    # Disease / therapeutic area
    "cancer", "tumor", "tumour", "oncology", "leukemia", "leukaemia",
    "lymphoma", "melanoma", "myeloma", "carcinoma", "glioblastoma",
    "nsclc", "sclc", "diabetes", "cardiovascular", "alzheimer",
    "parkinson", "psoriasis", "rheumatoid", "multiple sclerosis", "rare disease",
    # Treatment / clinical
    "immunotherapy", "chemotherapy", "radiotherapy", "radiation therapy",
    "clinical trial", "randomized", "placebo", "biomarker",
    "overall survival", "progression-free", "adverse event",
    "pd-l1", "pd-1", "her2", "egfr", "alk", "braf", "kras", "brca",
    "biologic", "biosimilar", "monoclonal antibody",
    # Regulatory / industry
    "fda", "pharmaceutical", "pharma", "drug approval", "drug development",
    "medical affairs", "real-world evidence", "health technology", "market access",
    # Company names
    "roche", "novartis", "pfizer", "bayer", "sanofi", "astrazeneca",
    "genentech", "merck", "bristol myers", "gilead", "amgen",
    "abbvie", "regeneron", "eli lilly", "moderna",
    # Brand drugs (oncology focus)
    "keytruda", "opdivo", "tecentriq", "herceptin", "avastin",
    "osimertinib", "alectinib", "pembrolizumab", "nivolumab", "atezolizumab",
    "palbociclib", "ribociclib", "ibrutinib", "venetoclax", "rituximab",
    # Congresses
    "asco", "esmo", "aacr", "sitc",
    # Healthcare context
    "oncologist", "hematologist", "patient outcomes", "health outcomes",
})


def _is_pharma_relevant(post: dict) -> bool:
    """Return True if the post has any pharma/medical signal in text, hashtags, or topic."""
    text = " ".join(filter(None, [
        post.get("text") or "",
        " ".join(post.get("hashtags") or []),
        post.get("author") or "",
        post.get("topic") or "",
    ])).lower()
    return any(sig in text for sig in _PHARMA_SIGNALS)

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

_STATUS_KEY = "social_scan:status"


def _set_status(**fields) -> None:
    try:
        import redis as _redis
        from app.config import get_settings
        r = _redis.Redis.from_url(get_settings().redis_url, socket_timeout=2)
        existing = {}
        cur = r.get(_STATUS_KEY)
        if cur:
            try:
                existing = json.loads(cur)
            except Exception:
                existing = {}
        existing.update(fields)
        r.set(_STATUS_KEY, json.dumps(existing), ex=86400)
    except Exception:
        pass


@celery_app.task(
    bind=True,
    name="app.tasks.social.social_scan",
    queue="scrape",
    # Expensive (real Apify $) + manually triggered: never auto-requeue. A killed
    # or worker-lost scan is lost, not re-run — re-running would double-spend credits.
    acks_late=False,
    reject_on_worker_lost=False,
    max_retries=0,
    soft_time_limit=3000,
    time_limit=3300,
)
def social_scan(self, lang_override: str | None = None) -> dict:
    """Run a full social trend scan. lang_override: 'fr'|'en'|'all' to override settings."""
    import asyncio
    return asyncio.run(_run_scan(lang_override))


async def _run_scan(lang_override: str | None = None) -> dict:
    from datetime import datetime, timezone
    from app.database import CelerySessionLocal
    from app.models import AppSettings, SocialPost, Target
    from app.services import apify_client
    from app.services.deduplicator import sha256_hash
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    if not apify_client.is_configured():
        logger.warning("social_scan.no_apify_token")
        _set_status(running=False, error="APIFY_API_TOKEN not set")
        return {"error": "apify_not_configured"}

    # ── Load config ────────────────────────────────────────
    async with CelerySessionLocal() as sess:
        s = await sess.get(AppSettings, 1)
        keywords = json.loads(s.social_keywords) if s and s.social_keywords else []
        platforms = json.loads(s.social_platforms) if s and s.social_platforms else \
            ["instagram", "twitter", "linkedin", "facebook"]
        window = s.social_window_days if s else 180
        max_per_query = s.social_max_per_query if s else 30
        include_kols = s.social_include_kols if s else True
        fb_page_urls = json.loads(s.facebook_page_urls) if s and s.facebook_page_urls else []
        lang_filter = lang_override or getattr(s, "social_lang_filter", "fr") or "fr"

        # List of (search_term, platform_hint) for KOL scanning.
        # Prefer twitter_handle over name for Twitter (more precise); name is used as fallback.
        kol_names: list[str] = []
        kol_twitter_handles: list[str] = []
        if include_kols:
            rows = await sess.execute(
                select(Target.name, Target.twitter_handle).where(Target.active == True)  # noqa: E712
            )
            for name, handle in rows.all():
                kol_names.append(name)
                if handle:
                    kol_twitter_handles.append(handle.lstrip("@"))
                else:
                    kol_twitter_handles.append(name)

    # Facebook uses page URLs, not keywords — handle it as one separate job.
    # All other platforms use keyword/hashtag terms.
    non_fb = [p for p in platforms if p != "facebook"]
    HASHTAG_PLATFORMS = {"instagram"}

    terms: list[tuple[str, str, list[str]]] = []
    for kw in keywords:
        terms.append((kw, "field", non_fb))
    # For KOLs: use twitter_handle on Twitter (precise handle search), name on other platforms
    kol_platforms = [p for p in non_fb if p not in HASHTAG_PLATFORMS]
    if kol_platforms and kol_names:
        twitter_in_kol = "twitter" in kol_platforms
        other_kol_platforms = [p for p in kol_platforms if p != "twitter"]
        for i, name in enumerate(kol_names):
            handle = kol_twitter_handles[i] if i < len(kol_twitter_handles) else name
            if twitter_in_kol:
                terms.append((handle, "kol", ["twitter"]))
            if other_kol_platforms:
                terms.append((name, "kol", other_kol_platforms))

    # One Facebook job using all configured page URLs (if FB is enabled and URLs set)
    fb_job = "facebook" in platforms and bool(fb_page_urls)

    if not terms and not fb_job:
        logger.warning("social_scan.no_terms")
        _set_status(running=False, error="No keywords, KOLs, or Facebook pages configured")
        return {"error": "no_terms"}

    total_jobs = sum(len(p) for _, _, p in terms) + (1 if fb_job else 0)
    _set_status(running=True, error=None, total=total_jobs, done=0,
                inserted=0, started_at=datetime.now(timezone.utc).isoformat())
    logger.info("social_scan.start", terms=len(terms), fb_job=fb_job, jobs=total_jobs)

    loop = asyncio.get_running_loop()
    # Bounded concurrency: max 4 simultaneous Apify runs to avoid saturating the
    # account and hitting Apify's concurrent-run limit.
    sem = asyncio.Semaphore(4)
    done_count = 0
    inserted_count = 0
    lock = asyncio.Lock()

    async def _run_one(term: str, kind: str, platform: str) -> None:
        nonlocal done_count, inserted_count
        async with sem:
            # Always scrape worldwide — language is detected per post and
            # stored. The UI's FR/EN/Global filter is display-only, so the
            # same scrape serves all modes (no double Apify cost).
            posts = await loop.run_in_executor(
                None,
                lambda p=platform, t=term: apify_client.fetch_platform(
                    p, t, max_results=max_per_query, window_days=window,
                    page_urls=fb_page_urls if p == "facebook" else None,
                    lang_filter=None,
                ),
            )
        local_inserted = 0
        for post in posts:
            post["topic"] = term  # ensure topic is set before relevance check
            if not _is_pharma_relevant(post):
                logger.debug("social_scan.filtered_irrelevant", platform=post.get("platform"), url=post.get("post_url", "")[:80])
                continue
            # NOTE: posts saved regardless of language to maximize Apify ROI.
            # Language is detected and stored; UI filters by language at display time.
            ch = sha256_hash(post["post_url"])
            stmt = pg_insert(SocialPost).values(
                platform=post["platform"],
                post_url=post["post_url"],
                author=post.get("author"),
                text=post.get("text"),
                thumbnail_url=post.get("thumbnail_url"),
                likes=post.get("likes", 0),
                comments=post.get("comments", 0),
                views=post.get("views", 0),
                shares=post.get("shares", 0),
                hashtags=json.dumps(post.get("hashtags", [])),
                query=term,
                kind=kind,
                topic=term,
                language=_detect_lang(post.get("text", "")),
                posted_at=post.get("posted_at"),
                content_hash=ch,
            ).on_conflict_do_nothing(index_elements=["content_hash"])
            async with CelerySessionLocal() as wsess:
                try:
                    res = await wsess.execute(stmt)
                    await wsess.commit()
                    if res.rowcount:
                        local_inserted += 1
                except Exception as exc:
                    await wsess.rollback()
                    logger.debug("social_scan.insert_failed", exc=str(exc)[:120])
        async with lock:
            done_count += 1
            inserted_count += local_inserted
            _set_status(done=done_count, inserted=inserted_count)

    jobs = [
        _run_one(term, kind, platform)
        for term, kind, term_platforms in terms
        for platform in term_platforms
    ]
    if fb_job:
        jobs.append(_run_one("", "field", "facebook"))
    await asyncio.gather(*jobs, return_exceptions=True)

    _set_status(running=False, done=done_count, inserted=inserted_count,
                finished_at=datetime.now(timezone.utc).isoformat())
    logger.info("social_scan.done", jobs=done_count, inserted=inserted_count)
    return {"jobs": done_count, "inserted": inserted_count}


def _expand_query(query: str) -> dict[str, list[str]]:
    """Use LLM to generate platform-split search terms from a natural-language query.

    Returns {"hashtags": [...], "keywords": [...]}:
    - hashtags: no spaces, for Instagram (actor batches them in one call)
    - keywords: phrases/terms for Twitter (OR-joined), LinkedIn, Facebook

    Falls back to raw query on LLM failure.
    """
    import json as _json
    from app.services.llm_router import call_llm

    prompt = (
        "You are a pharma social media intelligence expert focused on the FRENCH market.\n"
        "Given a user's search query, generate search terms for social media scrapers.\n"
        "Bias the expansion towards France: include the French translation of the topic, "
        "French medical hashtags, and a couple of global English terms so worldwide posts are still captured.\n\n"
        "Return ONLY a JSON object with two keys:\n"
        '- "hashtags": exactly 5 terms for Instagram (no spaces, no # prefix). '
        "Mix: 3-4 French/France-related + 1-2 English/global. Use real French medical hashtags. "
        "Keep this list short — each hashtag costs Apify credits.\n"
        '- "keywords": 6-8 terms for Twitter/LinkedIn/Facebook (can include spaces and phrases). '
        "Mix: 4-5 in French + 2-3 in English. These are free via TinyFish.\n\n"
        "Focus on: drug names (universal), French disease names, French treatment terms, "
        "French institutions (INCa, Ligue contre le cancer, ARC, Unicancer, Inserm), "
        "patient communities (octobrerose, marsbleu), congresses (also in French).\n\n"
        "Examples:\n"
        '- "lung cancer" → {"hashtags": ["cancerdupoumon", "poumon", "oncologiefrance", '
        '"octobrerose", "cancersurvivants", "lungcancer", "NSCLC"], '
        '"keywords": ["cancer du poumon", "cancer poumon France", "oncologie pulmonaire", '
        '"essai clinique poumon", "Ligue contre le cancer poumon", "lung cancer", "NSCLC research"]}\n'
        '- "Tecentriq" → {"hashtags": ["Tecentriq", "atezolizumab", "immunothérapie", '
        '"oncologiefrance", "essaiclinique", "immunotherapy", "pdl1"], '
        '"keywords": ["Tecentriq", "atezolizumab", "immunothérapie Roche", '
        '"Tecentriq France", "essai clinique atezolizumab", "atezolizumab clinical trial"]}\n'
        '- "ASCO 2026" → {"hashtags": ["ASCO2026", "ASCO", "oncologiefrance", '
        '"congresoncologie", "rechercheclinique", "oncology2026"], '
        '"keywords": ["ASCO 2026", "ASCO 2026 France", "congrès oncologie ASCO", '
        '"actualité oncologie", "ASCO annual meeting"]}\n\n'
        f'Query: "{query}"'
    )
    fallback = {"hashtags": [query], "keywords": [query]}
    try:
        reply = call_llm([{"role": "user", "content": prompt}], temperature=0.0, max_tokens=400)
        reply = reply.strip()
        if "```" in reply:
            reply = reply.split("```")[1].lstrip("json").strip()
        parsed = _json.loads(reply)
        if isinstance(parsed, dict):
            # Hashtag cap = 5 to control Apify Instagram cost (per-hashtag billing).
            # Keywords cap = 8 (TinyFish for Twitter/LinkedIn is not per-query billed).
            ht = [t.strip() for t in parsed.get("hashtags", []) if isinstance(t, str) and t.strip()][:5]
            kw = [t.strip() for t in parsed.get("keywords", []) if isinstance(t, str) and t.strip()][:8]
            if ht or kw:
                return {"hashtags": ht or [query], "keywords": kw or [query]}
    except Exception as exc:
        logger.warning("discover_fetch.expand_failed", query=query, exc=str(exc)[:120])
    return fallback


_DISCOVER_STATUS_KEY = "social_discover:status:{q}"


def _set_discover_status(query: str, **fields) -> None:
    try:
        import redis as _redis
        from app.config import get_settings
        r = _redis.Redis.from_url(get_settings().redis_url, socket_timeout=2)
        r.set(_DISCOVER_STATUS_KEY.format(q=query.lower().strip()),
              json.dumps(fields), ex=3600)
    except Exception:
        pass


@celery_app.task(
    bind=True,
    name="app.tasks.social.discover_fetch",
    queue="scrape",
    # Costs Apify $ per run — don't auto-requeue on timeout/worker loss.
    acks_late=False,
    reject_on_worker_lost=False,
    max_retries=0,
    soft_time_limit=600,
    time_limit=720,
)
def discover_fetch(self, query: str, lang_override: str | None = None) -> dict:
    """Ad-hoc bounded Apify fetch for a single Discovery query across platforms.
    lang_override: if set ('fr'|'en'|'all'), overrides AppSettings.social_lang_filter."""
    import asyncio
    return asyncio.run(_run_discover(query, lang_override))


async def _run_discover(query: str, lang_override: str | None = None) -> dict:
    from app.database import CelerySessionLocal
    from app.models import AppSettings, SocialPost
    from app.services import apify_client
    from app.services.deduplicator import sha256_hash
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    if not apify_client.is_configured():
        _set_discover_status(query, running=False, error="apify_not_configured")
        return {"error": "apify_not_configured"}

    async with CelerySessionLocal() as sess:
        s = await sess.get(AppSettings, 1)
        platforms = json.loads(s.social_platforms) if s and s.social_platforms else \
            ["instagram", "twitter", "linkedin", "facebook"]
        window = s.social_window_days if s else 180
        fb_page_urls = json.loads(s.facebook_page_urls) if s and s.facebook_page_urls else []
        lang_filter = lang_override or getattr(s, "social_lang_filter", "fr") or "fr"

    loop = asyncio.get_running_loop()

    # LLM understands the query and generates platform-appropriate search terms
    expanded = await loop.run_in_executor(None, lambda: _expand_query(query))
    hashtags = expanded["hashtags"]
    keywords = expanded["keywords"]
    logger.info("discover_fetch.start", query=query, hashtags=hashtags, keywords=keywords)
    _set_discover_status(query, running=True, error=None, inserted=0,
                         terms=list(dict.fromkeys(hashtags + keywords)))  # deduped union for UI

    # One actor call per platform with all terms batched — same cost as a single-term search
    async def _fetch_platform(p: str) -> list[dict]:
        # Worldwide scrape — UI's language filter is display-only,
        # so one Apify call serves all language modes.
        return await loop.run_in_executor(
            None,
            lambda: apify_client.fetch_platform_expanded(
                p, hashtags, keywords,
                max_results=30, window_days=window,
                page_urls=fb_page_urls if p == "facebook" else None,
                lang_filter=None,
            ),
        )

    fetch_results = await asyncio.gather(
        *[_fetch_platform(p) for p in platforms],
        return_exceptions=True,
    )

    # LLM-generated hashtags ARE the relevance gate — no additional pharma filter needed.
    # We still deduplicate on content_hash via ON CONFLICT DO NOTHING.
    inserted = 0
    for posts in fetch_results:
        if isinstance(posts, Exception) or not posts:
            continue
        for post in posts:
            # Tag with primary keyword as topic for display in trend chips
            post["topic"] = keywords[0] if keywords else query
            # Posts saved regardless of language — UI filters at display time
            ch = sha256_hash(post["post_url"])
            stmt = pg_insert(SocialPost).values(
                platform=post["platform"], post_url=post["post_url"],
                author=post.get("author"), text=post.get("text"),
                thumbnail_url=post.get("thumbnail_url"),
                likes=post.get("likes", 0), comments=post.get("comments", 0),
                views=post.get("views", 0), shares=post.get("shares", 0),
                hashtags=json.dumps(post.get("hashtags", [])),
                query=query, kind="field", topic=post["topic"],
                language=_detect_lang(post.get("text", "")),
                posted_at=post.get("posted_at"), content_hash=ch,
            ).on_conflict_do_nothing(index_elements=["content_hash"])
            async with CelerySessionLocal() as wsess:
                try:
                    r = await wsess.execute(stmt)
                    await wsess.commit()
                    if r.rowcount:
                        inserted += 1
                except Exception:
                    await wsess.rollback()

    all_terms = list(dict.fromkeys(hashtags + keywords))
    _set_discover_status(query, running=False, inserted=inserted, terms=all_terms)
    logger.info("discover_fetch.done", query=query, inserted=inserted, terms=all_terms)
    return {"inserted": inserted, "terms": all_terms}
