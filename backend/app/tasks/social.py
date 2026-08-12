"""Social trend scan — manual-trigger Apify scrape across platforms.

Reads the curated config from AppSettings (medical/drug/treatment keywords +
optionally KOL names), runs the per-platform Apify Actors, and ingests the
results into the social_posts table with real engagement counts. No LLM runs
during the scan — topic descriptions are generated on demand when the user
clicks a trend (see routers/social.py).
"""
import asyncio
import json

import structlog

from app.services.lang import detect_lang as _detect_lang
from app.services.fr_sources import Scope, is_french_source, normalize_host

FR_SCOPE = Scope.FR.value

# ── Pharma relevance gate ─────────────────────────────────
# Posts are only stored if they contain at least one of these signals in
# their text, hashtags, or topic. This filters out off-topic results that
# happen to mention a vague keyword (e.g. "drug" = illegal drugs, slang).
#
# This gate runs at INGEST, before insert — anything it rejects is a post we
# already paid Apify for and then threw away. It used to be English-only, so
# French posts ("essai clinique", "immunothérapie", "dépistage") were dropped
# on arrival. That was half of the client's "not enough French posts".
#
# Matching is substring-based on lowercased text, so terms must be long enough
# to be unambiguous: "has" (HAS, the French health authority) would match every
# English sentence, and "arc" would match "search". Use a longer phrase instead.
# Accented terms are listed with their unaccented spelling too, because people
# type "immunotherapie" on mobile.
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

    # ── FRENCH ─────────────────────────────────────────────
    # Disease / therapeutic area
    "cancer du poumon", "cancer bronchique", "cancer du sein",
    "cancer colorectal", "cancer de la prostate", "cancer de l'ovaire",
    "leucémie", "leucemie", "lymphome", "myélome", "myelome",
    "mélanome", "melanome", "tumeur", "métastase", "metastase",
    "métastatique", "metastatique", "carcinome", "sarcome",
    # CBNPC / CPNPC are what French clinicians write for NSCLC; CPC for SCLC.
    "cbnpc", "cpnpc", "oncologie", "cancérologie", "cancerologie",
    "pneumologie", "hématologie", "hematologie",
    "sclérose en plaques", "sclerose en plaques", "maladie rare", "hémophilie",
    "hemophilie", "diabète", "diabete",
    # Treatment / clinical
    "immunothérapie", "immunotherapie", "chimiothérapie", "chimiotherapie",
    "radiothérapie", "radiotherapie", "thérapie ciblée", "therapie ciblee",
    "essai clinique", "essai de phase", "recherche clinique", "biomarqueur",
    "dépistage", "depistage", "survie globale", "survie sans progression",
    "effets secondaires", "effets indésirables", "effets indesirables",
    "soins de support", "prise en charge", "médicament", "medicament",
    "anticorps monoclonal", "biosimilaire", "médecine personnalisée",
    "medecine personnalisee", "traitement du cancer", "essais cliniques",
    # Regulatory / industry / institutions (long forms only — see note above)
    "institut national du cancer", "ligue contre le cancer", "fondation arc",
    "gustave roussy", "institut curie", "unicancer", "inserm",
    "haute autorité de santé", "haute autorite de sante",
    "agence du médicament", "agence du medicament", "affaires médicales",
    # Healthcare roles
    "oncologue", "pneumologue", "hématologue", "hematologue",
    "médecin", "medecin", "soignant", "cancérologue", "cancerologue",
})


def _is_pharma_relevant(post: dict) -> bool:
    """Return True if the post should be stored.

    Posts from a curated French source bypass the keyword gate. The gate exists
    to reject off-topic results from a broad keyword search, but a post from a
    source we deliberately chose — Gustave Roussy, INCa, Le Quotidien du Médecin —
    is on-topic by construction. Without this bypass, source pinning makes the
    yield *worse*: an institution's congress-programme announcement carries no
    pharma keyword, so it is paid for and then discarded.
    """
    if is_french_source(post.get("post_url") or ""):
        return True
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

# Comments are billed per post, so only the most-discussed posts are worth
# scraping — the long tail has none.
_COMMENT_POST_LIMIT = 10
# Patient comments are the most sensitive material the platform touches.
# They are stored through the same AE classification as every other post.
_COMMENTS_ENABLED = True
# Free, but each term is still a search against the shared rate limiter.
_IG_FREE_TERM_LIMIT = 12


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


async def _tracked_handles(session, platform: str) -> list[str]:
    """Active handles for one platform, newest first. [] means 'none configured',
    which the search builder treats as 'do not account-pin', while None means
    'caller supplied nothing' and falls back to the curated constant."""
    from sqlalchemy import select

    from app.models import TrackedAccount

    rows = await session.execute(
        select(TrackedAccount.handle)
        .where(TrackedAccount.platform == platform, TrackedAccount.active.is_(True))
        .order_by(TrackedAccount.id)
    )
    return [h for h in rows.scalars().all() if h]


async def _ingest_posts(session, posts: list[dict], *, kind: str, topic: str,
                        query: str) -> int:
    """Insert normalised posts, sharing one code path with the keyword scan.

    Comments come through here too, so they get the same dedup hash, language
    detection, provenance and — critically — the same adverse-event handling as
    any other post.
    """
    from app.models import SocialPost
    from app.services.deduplicator import sha256_hash
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    inserted = 0
    for post in posts:
        url = post.get("post_url")
        if not url:
            continue
        if not _is_pharma_relevant({**post, "topic": topic}):
            continue
        stmt = pg_insert(SocialPost).values(
            platform=post["platform"], post_url=url,
            parent_url=post.get("parent_url"),
            author=post.get("author"), text=post.get("text"),
            thumbnail_url=post.get("thumbnail_url"),
            likes=post.get("likes", 0), comments=post.get("comments", 0),
            views=post.get("views", 0), shares=post.get("shares", 0),
            hashtags=json.dumps(post.get("hashtags", [])),
            query=query, kind=kind, topic=topic,
            language=_detect_lang(post.get("text", "")),
            domain=normalize_host(url),
            source_scope=(FR_SCOPE if is_french_source(url) else Scope.GLOBAL.value),
            posted_at=post.get("posted_at"),
            content_hash=sha256_hash(url),
        ).on_conflict_do_nothing(index_elements=["content_hash"])
        try:
            result = await session.execute(stmt)
            await session.commit()
            if result.rowcount:
                inserted += 1
        except Exception as exc:
            await session.rollback()
            logger.debug("social_scan.insert_failed", exc=str(exc)[:120])
    return inserted


async def _scan_instagram_free(terms: list[str], lang_filter: str | None,
                               max_per_term: int = 10) -> int:
    """Instagram discovery through TinyFish search — no Apify credits.

    The web index knows Instagram posts, returns the French caption in the
    snippet, and honours --location France, which the hashtag actor cannot do at
    all. It costs nothing, so it runs *alongside* the Apify hashtag lane rather
    than replacing it: Apify still supplies the engagement counts and timestamps
    that search results lack, and the client's "top 10 posts by views/comments"
    depends on those.

    Overlap between the two lanes is harmless — both dedup on the URL hash.
    """
    from app.database import CelerySessionLocal
    from app.services.tinyfish_social import fetch_via_tinyfish

    if not terms:
        return 0
    loop = asyncio.get_running_loop()
    saved = 0
    for term in terms:
        posts = await loop.run_in_executor(
            None, lambda t=term: fetch_via_tinyfish(
                "instagram", [t], max_results=max_per_term, lang_filter=lang_filter))
        if not posts:
            continue
        async with CelerySessionLocal() as sess:
            saved += await _ingest_posts(sess, posts, kind="field", topic=term,
                                         query=term)
    logger.info("social_scan.instagram_free", terms=len(terms), posts=saved)
    return saved


async def _scan_instagram_accounts(handles: list[str], window: int,
                                   max_per_account: int, with_comments: bool) -> tuple[int, int]:
    """Scrape tracked Instagram accounts, then the comments under what they posted.

    Account posts are on-topic by construction, so this is the highest-yield
    Instagram lane — the hashtag actor can never reach a named account.
    """
    from app.database import CelerySessionLocal
    from app.services import apify_client

    if not handles:
        return 0, 0

    loop = asyncio.get_running_loop()
    posts = await loop.run_in_executor(
        None, lambda: apify_client.fetch_instagram_accounts(
            handles, max_per_account=max_per_account, window_days=window))
    if not posts:
        return 0, 0

    async with CelerySessionLocal() as sess:
        saved = await _ingest_posts(sess, posts, kind="account",
                                    topic="tracked account", query="tracked:instagram")

    comments_saved = 0
    if with_comments and posts:
        # Comment-scrape the most engaged posts only: comments are billed per
        # post and the long tail carries almost no discussion.
        top = sorted(posts, key=lambda p: (p.get("comments") or 0), reverse=True)
        urls = [p["post_url"] for p in top[:_COMMENT_POST_LIMIT] if (p.get("comments") or 0) > 0]
        if urls:
            comments = await loop.run_in_executor(
                None, lambda: apify_client.fetch_instagram_comments(urls))
            async with CelerySessionLocal() as sess:
                comments_saved = await _ingest_posts(
                    sess, comments, kind="comment", topic="comment",
                    query="tracked:instagram:comments")
    logger.info("social_scan.instagram_accounts", accounts=len(handles),
                posts=saved, comments=comments_saved)
    return saved, comments_saved


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

        # Accounts the team chose to monitor. Loaded once per scan and threaded
        # down, rather than queried deep inside the search builder where there is
        # no session. Facebook pages still come from AppSettings for now; the
        # registry holds them too, so that is the next thing to converge.
        tracked_x = await _tracked_handles(sess, "twitter")
        tracked_ig = await _tracked_handles(sess, "instagram")

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
        local_inserted = 0
        # Fetch AND insert stay inside the semaphore so concurrent DB
        # connections are bounded to 4 alongside the Apify runs.
        async with sem:
            # Target the search at the configured market instead of scraping
            # worldwide and filtering afterwards. Display-side filtering can
            # only subtract: if 5% of a worldwide haul is French, the French
            # view is 5%. Searching in French fills the same result slots with
            # French posts at identical cost.
            posts = await loop.run_in_executor(
                None,
                lambda p=platform, t=term, lf=lang_filter: apify_client.fetch_platform(
                    p, t, max_results=max_per_query, window_days=window,
                    page_urls=fb_page_urls if p == "facebook" else None,
                    lang_filter=lf, accounts=tracked_x,
                ),
            )
            # ONE session (= one engine + one connection) for the whole batch —
            # the old per-post CelerySessionLocal opened and tore down a fresh
            # engine + TCP connection for every single post. Commit stays
            # per-post (cheap on an open connection) so one bad row never
            # discards its siblings.
            async with CelerySessionLocal() as wsess:
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
                        domain=normalize_host(post["post_url"]),
                        source_scope=(FR_SCOPE if is_french_source(post["post_url"])
                                      else Scope.GLOBAL.value),
                        posted_at=post.get("posted_at"),
                        content_hash=ch,
                    ).on_conflict_do_nothing(index_elements=["content_hash"])
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
    # Tracked Instagram accounts use a different actor from the hashtag scan, so
    # they run as their own job rather than through _run_one.
    if "instagram" in platforms:
        # Free French discovery, run in addition to the paid hashtag lane.
        jobs.append(_scan_instagram_free(keywords[:_IG_FREE_TERM_LIMIT], lang_filter))
        if tracked_ig:
            jobs.append(_scan_instagram_accounts(
                tracked_ig, window, max_per_query, _COMMENTS_ENABLED))
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
        "You are a pharma social media intelligence expert working the FRENCH market.\n"
        "Given a user's search query, generate search terms for social media scrapers.\n"
        "This platform monitors France only — French KOLs, French institutions, French "
        "patients. Write the terms the way a French oncologist or a French patient would "
        "actually type them, NOT an English term translated word for word.\n\n"
        "Return ONLY a JSON object with two keys:\n"
        '- "hashtags": exactly 5 terms for Instagram (no spaces, no # prefix). '
        "All must be French or France-specific, except drug brand names and congress names, "
        "which are identical in every language. Use real French medical hashtags. "
        "Keep this list short — each hashtag costs Apify credits.\n"
        '- "keywords": 6-8 terms for Twitter/LinkedIn/Facebook (spaces and phrases allowed). '
        "At least 6 must be in French. These are free via TinyFish, so favour recall.\n\n"
        "Use the abbreviations French clinicians actually use: CBNPC (never NSCLC), "
        "CPC (never SCLC), SG for survie globale, SSP for survie sans progression.\n"
        "Draw on: French disease names, French treatment terms, French institutions "
        "(INCa, Unicancer, Inserm, Gustave Roussy, Institut Curie, IFCT, SPLF, "
        "Ligue contre le cancer, Fondation ARC), French patient communities "
        "(octobrerose, marsbleu), and congresses named in French.\n\n"
        "Examples:\n"
        '- "lung cancer" → {"hashtags": ["cancerdupoumon", "CBNPC", "oncologiefrance", '
        '"pneumologie", "cancersurvivants"], '
        '"keywords": ["cancer du poumon", "cancer bronchique", "CBNPC", '
        '"oncologie pulmonaire France", "essai clinique poumon", '
        '"Ligue contre le cancer poumon", "dépistage cancer du poumon"]}\n'
        '- "Tecentriq" → {"hashtags": ["Tecentriq", "atezolizumab", "immunothérapie", '
        '"oncologiefrance", "essaiclinique"], '
        '"keywords": ["Tecentriq", "atezolizumab", "immunothérapie Roche", '
        '"Tecentriq France", "essai clinique atezolizumab", "immunothérapie CBNPC", '
        '"atezolizumab survie globale"]}\n'
        '- "ASCO 2026" → {"hashtags": ["ASCO2026", "oncologiefrance", '
        '"congresoncologie", "rechercheclinique", "cancerologie"], '
        '"keywords": ["ASCO 2026", "ASCO 2026 France", "congrès oncologie ASCO", '
        '"actualité oncologie France", "résultats ASCO 2026", "étude présentée ASCO"]}\n\n'
        f'Query: "{query}"'
    )
    fallback = {"hashtags": [query], "keywords": [query]}
    try:
        # 2048, not 400: gemini-2.5-flash is a *thinking* model and its reasoning
        # tokens come out of the same budget, so 400 returned ~30 characters —
        # the JSON was always truncated, this function always fell back to the raw
        # query, and the French term expansion never actually ran. Same root cause
        # as the extractor truncation fixed on 2026-08-08.
        reply = call_llm([{"role": "user", "content": prompt}], temperature=0.0, max_tokens=2048)
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
        # Market-targeted, not worldwide — see the note in _run_scan. The terms
        # themselves are already French-biased by _expand_query; lang_filter
        # adds the platform-native targeting on top.
        return await loop.run_in_executor(
            None,
            lambda p=p, lf=lang_filter: apify_client.fetch_platform_expanded(
                p, hashtags, keywords,
                max_results=30, window_days=window,
                page_urls=fb_page_urls if p == "facebook" else None,
                lang_filter=lf,
            ),
        )

    fetch_results = await asyncio.gather(
        *[_fetch_platform(p) for p in platforms],
        return_exceptions=True,
    )

    # LLM-generated hashtags ARE the relevance gate — no additional pharma filter needed.
    # We still deduplicate on content_hash via ON CONFLICT DO NOTHING.
    # ONE session for the whole ingest — the old code created a fresh engine +
    # TCP connection per post. Per-post commit on the open connection keeps
    # one bad row from discarding the rest.
    inserted = 0
    async with CelerySessionLocal() as wsess:
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
                    domain=normalize_host(post["post_url"]),
                    source_scope=(FR_SCOPE if is_french_source(post["post_url"])
                                  else Scope.GLOBAL.value),
                    posted_at=post.get("posted_at"), content_hash=ch,
                ).on_conflict_do_nothing(index_elements=["content_hash"])
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
