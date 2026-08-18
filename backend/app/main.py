import logging
import structlog
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from sqlalchemy import text
from app.config import get_settings
from app.database import engine, Base
from app.routers import targets, runs, reports, settings as settings_router, agent
from app.routers import accounts as accounts_router
from app.routers import discovery as discovery_router
from app.routers import transparence as transparence_router
from app.routers import market_access as market_access_router
from app.routers import social as social_router
from app.routers import auth as auth_router
from app.routers import burning_topics as burning_topics_router
from app.routers import congress as congress_router
from app.auth import require_admin, daily_gen_guard, get_current_user, daily_generation_available
from app.services.ae_filter import insight_not_ae, post_not_ae, social_not_ae

_settings = get_settings()

# ── Logging: console + JSON file in /tmp (kept outside the repo) ──
LOG_FILE = Path("/tmp/pharmaradar-backend.log")

_log_level = logging.getLevelName(_settings.log_level)

# Stdlib root logger → JSON file. Also attached to uvicorn loggers so access logs land here too.
_file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
_file_handler.setLevel(_log_level)
_file_handler.setFormatter(logging.Formatter("%(message)s"))
logging.basicConfig(level=_log_level, handlers=[_file_handler, logging.StreamHandler()], force=True)

# Route uvicorn / celery loggers through root (which has the file handler).
# Clear their own handlers and enable propagation so records hit the file exactly once.
for _uv in ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi", "celery", "celery.task"):
    _l = logging.getLogger(_uv)
    _l.handlers = []
    _l.propagate = True
    _l.setLevel(_log_level)

# Silence noisy stdlib loggers so the log view stays useful
for _noisy in ("sqlalchemy.engine", "sqlalchemy.engine.Engine", "watchfiles",
               "watchfiles.main", "httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(_log_level),
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)

# ── Sentry ────────────────────────────────────────────────
if _settings.sentry_dsn:
    import sentry_sdk
    sentry_sdk.init(dsn=_settings.sentry_dsn, environment=_settings.environment, traces_sample_rate=0.1)


_DEFAULT_SECRET = "changeme-at-least-32-chars-long!!"

# Standard reporting window for briefs, syntheses and dashboard stats (client
# spec: reports and summaries default to the last 30 days). This is a *read*
# window only — the scrape window (AppSettings.social_window_days) stays deeper
# so widening a view costs nothing, the data is already there.
BRIEF_WINDOW_DAYS = 30


def _verify_secret_key() -> None:
    """Refuse to boot with a forgeable JWT key. The default is public (in the
    repo), so running it in production lets anyone mint admin tokens."""
    key = _settings.secret_key
    weak = key == _DEFAULT_SECRET or len(key) < 32
    if not weak:
        return
    if _settings.is_production:
        raise RuntimeError(
            "FATAL: SECRET_KEY is unset/weak in production. JWTs would be forgeable. "
            "Set a strong random SECRET_KEY env var "
            "(python -c \"import secrets; print(secrets.token_urlsafe(48))\")."
        )
    logger.warning("startup.weak_secret_key",
                   hint="Using the insecure default SECRET_KEY — fine for local dev, NEVER in prod.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _verify_secret_key()
    logger.info("startup", env=_settings.environment)
    async with engine.begin() as conn:
        # Enable pgvector if available — silently skip if not installed locally
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception:
            pass
        try:
            await conn.run_sync(Base.metadata.create_all)
        except Exception as _e:
            if "vector" in str(_e).lower():
                logger.warning("startup.create_all_skipped_vector_missing",
                               hint="Install pgvector or run against Railway DB")
            else:
                raise
    await _seed_defaults()
    await _seed_admin()
    yield
    logger.info("shutdown")
    await engine.dispose()


async def _seed_admin() -> None:
    """Create the first admin from SEED_ADMIN_* env vars if no users exist."""
    from app.database import AsyncSessionLocal
    from app.auth import ensure_seed_admin
    try:
        async with AsyncSessionLocal() as sess:
            await ensure_seed_admin(sess)
    except Exception as exc:
        logger.warning("startup.seed_admin_failed", exc=str(exc)[:160])


async def _seed_defaults() -> None:
    """Seed AppSettings singleton and optional target pre-load from targets.json."""
    from app.database import AsyncSessionLocal
    from app.models import AppSettings, Target
    from sqlalchemy import text
    import json

    async with AsyncSessionLocal() as sess:
        # Serialize this whole routine across concurrent uvicorn workers. On
        # first boot against an empty DB, two workers can both see empty
        # tables at once and race to insert the same rows — whichever worker
        # loses a unique-constraint race crashes, and uvicorn kills the whole
        # server if any worker fails to start. This advisory lock makes the
        # second worker simply wait, then find everything already seeded.
        await sess.execute(text("SELECT pg_advisory_lock(727271)"))
        try:
            await _seed_defaults_locked(sess)
        finally:
            await sess.execute(text("SELECT pg_advisory_unlock(727271)"))


async def _seed_defaults_locked(sess) -> None:
    from app.models import AppSettings, Target
    import json

    if True:
        s = await sess.get(AppSettings, 1)
        if not s:
            # Pick the best available provider based on which API key is in .env
            # Priority: Gemini (fast+cheap) → NVIDIA (fallback) → others
            if _settings.gemini_api_key:
                provider, model = "gemini", "gemini-2.5-flash"
            elif _settings.nvidia_api_key:
                provider, model = "nvidia", "meta/llama-3.3-70b-instruct"
            elif _settings.anthropic_api_key:
                provider, model = "anthropic", "claude-haiku-4-5-20251001"
            elif _settings.openai_api_key:
                provider, model = "openai", "gpt-4o-mini"
            elif _settings.openrouter_api_key:
                provider, model = "openrouter", "openai/gpt-4o-mini"
            else:
                provider, model = "vertex", "gemini-2.5-flash"
            # on_conflict_do_nothing: with multiple uvicorn workers, two processes
            # can both see no row on startup and race to insert id=1 — a plain
            # INSERT crashes one worker with UniqueViolationError (and uvicorn
            # kills the whole server if any worker fails to start). This makes
            # the seed atomic at the DB level instead of relying on the
            # check-then-insert above.
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            stmt = pg_insert(AppSettings).values(
                id=1, llm_provider=provider, llm_model=model
            ).on_conflict_do_nothing(index_elements=["id"])
            await sess.execute(stmt)
            await sess.commit()
            logger.info("seeded_app_settings", provider=provider)
            s = await sess.get(AppSettings, 1)

        # Seed default Facebook page URLs if none set yet.
        # Uses apify/facebook-posts-scraper with known pharma/oncology/medical pages
        # (keyword search doesn't work on FB without auth; page-URL scraping does).
        if s and not s.facebook_page_urls:
            default_fb_pages = [
                # Roche France + global
                "https://www.facebook.com/roche",
                "https://www.facebook.com/RocheFrance",
                # French pharma & health institutions
                "https://www.facebook.com/sanofi",
                "https://www.facebook.com/INCa.Institut.National.Cancer",  # Institut National du Cancer
                "https://www.facebook.com/liguecancerfrance",               # Ligue contre le cancer
                "https://www.facebook.com/fondationARC",                    # ARC cancer research
                "https://www.facebook.com/unicancer.fr",                    # Unicancer
                "https://www.facebook.com/inserm.fr",                       # INSERM
                "https://www.facebook.com/has.sante",                       # HAS (French health authority)
                "https://www.facebook.com/ansm.sante.fr",                   # ANSM (French medicines agency)
                # French patient communities
                "https://www.facebook.com/RespirEspoir",                    # Lung cancer France
                "https://www.facebook.com/Cancer.Info.Service",
                # Oncology congresses — kept despite being international: a
                # congress is definitionally global, and these pages are the
                # source for the congress module.
                "https://www.facebook.com/ASCO.org",
                "https://www.facebook.com/esmo.oncology",
            ]
            # Global pharma and WHO pages were removed from the seed on
            # 2026-08-12: Facebook is the one platform where the source is
            # chosen outright, so spending those slots on worldwide corporate
            # feeds contradicted the France-first requirement. Competitors are
            # tracked through their French affiliates instead (see
            # services/fr_sources.FR_PHARMA). Add them back per-page in
            # Settings if worldwide competitor coverage is wanted.
            s.facebook_page_urls = json.dumps(default_fb_pages)
            await sess.commit()
            logger.info("seeded_facebook_page_urls", count=len(default_fb_pages))

        # Seed a starter social-scan keyword list if none set yet
        if s and not s.social_keywords:
            default_keywords = [
                # Roche / Genentech brands
                # Roche brand & drug names (universal — no translation needed)
                "Tecentriq", "Ocrevus", "Hemlibra", "Kadcyla", "Perjeta",
                "Avastin", "Herceptin", "Polivy", "Lunsumio", "Roche", "Genentech",
                "RocheFrance",
                # Competitor drugs
                "Keytruda", "Opdivo", "Imfinzi", "Libtayo",
                # ── FRENCH oncology disease keywords (hashtag-safe, no spaces) ──
                "cancerdusein", "cancerdupoumon", "cancercolorectal",
                "cancerovaire", "cancerprostate", "leucémie", "lymphome",
                "myélome", "mélanome", "tumeur", "métastase",
                # French treatments / research
                "immunothérapie", "chimiothérapie", "essaiclinique",
                "rechercheclinique", "rechercheencancérologie", "biomarqueurs",
                "médecinepersonnalisée", "thérapieciblée", "oncologie",
                # French clinical shorthand — CBNPC is what French oncologists
                # write for NSCLC, so an English-only term list cannot reach them.
                "CBNPC", "cancerbronchique", "oncologiethoracique",
                "depistagepoumon", "therapieciblee", "soinsdesupport",
                "survieglobale", "GustaveRoussy", "InstitutCurie", "IFCT",
                # French patient communities
                "luttecontrelecancer", "patientsexperts", "cancersurvivants",
                "octobrerose", "marsbleu", "vaincrelecancer",
                # French congresses / institutions
                "ASCO2026", "ESMO2026", "INCa", "ligueducancer",
                "fondationARC", "unicancer", "InsermFrance",
                # English oncology fallbacks (some French KOLs post in English)
                "lungcancer", "NSCLC", "breastcancer", "immunotherapy",
                "clinicaltrial", "biomarker", "patientadvocacy",
                # Neurology / rare disease (French)
                "scléroseenplaques", "maladieraresfrance", "hémophilie",
            ]
            s.social_keywords = json.dumps(default_keywords)
            await sess.commit()
            logger.info("seeded_social_keywords", count=len(default_keywords))

        # Seed targets if file exists and table is empty
        targets_file = Path(__file__).parent / "targets.json"
        if targets_file.exists():
            from sqlalchemy import select, func
            count = await sess.execute(select(func.count()).select_from(Target))
            if count.scalar() == 0:
                raw = json.loads(targets_file.read_text())
                for item in raw:
                    sess.add(Target(
                        name=item["name"],
                        known_urls=json.dumps(item.get("known_urls", [])),
                        notes=item.get("notes"),
                    ))
                await sess.commit()
                logger.info("seeded_targets", count=len(raw))


app = FastAPI(
    title="PharmaRadar v3",
    version="3.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

# ── Blanket auth gate ─────────────────────────────────────
# Every /api/* route requires a valid signed token, except the public ones
# below. Per-endpoint require_admin / get_current_user still enforce roles and
# re-check the DB. Defined before CORS is added so CORS stays outermost and
# 401 responses still carry CORS headers (frontend can read them and redirect).
_PUBLIC_PREFIXES = ("/api/auth/login", "/api/docs", "/api/redoc", "/api/openapi")


@app.middleware("http")
async def require_auth(request, call_next):
    import jwt as _jwt
    from fastapi.responses import JSONResponse

    path = request.url.path
    if (request.method == "OPTIONS"
            or not path.startswith("/api")
            or path.startswith(_PUBLIC_PREFIXES)):
        return await call_next(request)

    # Internal service calls (beat scheduler → /api/runs/trigger) authenticate
    # with the SECRET_KEY-derived token instead of a user JWT.
    from app.auth import check_internal_token
    if check_internal_token(request.headers.get("X-Internal-Token")):
        return await call_next(request)

    header = request.headers.get("Authorization", "")
    token = header[7:].strip() if header[:7].lower() == "bearer " else ""
    if not token:
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    try:
        _jwt.decode(token, _settings.secret_key, algorithms=["HS256"])
    except Exception:
        return JSONResponse({"detail": "Invalid or expired token"}, status_code=401)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────
app.include_router(auth_router.router)
app.include_router(targets.router)
app.include_router(runs.router)
app.include_router(reports.router)
app.include_router(settings_router.router)
app.include_router(agent.router)
app.include_router(discovery_router.router)
app.include_router(social_router.router)
app.include_router(transparence_router.router)
app.include_router(market_access_router.router)
app.include_router(accounts_router.router)
app.include_router(burning_topics_router.router)
app.include_router(congress_router.router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "3.0.0"}


@app.get("/api/stats/topics")
async def stats_topics(days: int = BRIEF_WINDOW_DAYS, disease_area: str | None = None):
    """Return top discussed topics and categories for the dashboard graphs."""
    import math
    from app.database import AsyncSessionLocal
    from app.models import ExtractedInsight, Target, ScrapedPost
    from sqlalchemy import select, desc
    from datetime import datetime, timezone, timedelta
    from collections import Counter

    since = datetime.now(timezone.utc) - timedelta(days=days)
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as sess:
        q = (
            select(ExtractedInsight, Target, ScrapedPost)
            .join(Target, ExtractedInsight.target_id == Target.id)
            .join(ScrapedPost, ExtractedInsight.scraped_post_id == ScrapedPost.id)
            .where(ExtractedInsight.extracted_at >= since)
            .where(post_not_ae())
            .where(Target.target_type == "kol")   # competitor content has its own surfaces
            .order_by(desc(ExtractedInsight.extracted_at))
        )
        if disease_area and disease_area != "all":
            q = q.where(Target.disease_area == disease_area)
        rows = await sess.execute(q)
        insights = rows.all()

    cat_counts: Counter = Counter()
    topic_counts: Counter = Counter()
    topic_trend: dict[str, float] = {}
    topic_likes: dict[str, int] = {}
    topic_views: dict[str, int] = {}
    topic_urls: dict[str, str] = {}
    sentiment_counts: Counter = Counter({"positive": 0, "neutral": 0, "negative": 0})
    kol_counts: Counter = Counter()

    for ins, target, post in insights:
        cat = (ins.category or "other").replace("_", " ").title()
        cat_counts[cat] += 1
        if ins.topic:
            topic_counts[ins.topic] += 1
            # Recency-weighted trend score (5-day half-life)
            age_days = (now - ins.extracted_at).total_seconds() / 86400
            decay = math.exp(-age_days / 5)
            topic_trend[ins.topic] = topic_trend.get(ins.topic, 0.0) + decay
            topic_likes[ins.topic] = topic_likes.get(ins.topic, 0) + (post.likes or 0)
            topic_views[ins.topic] = topic_views.get(ins.topic, 0) + (post.views or 0)
            if ins.topic not in topic_urls and post.source_url:
                topic_urls[ins.topic] = post.source_url
        sentiment_counts[(ins.sentiment or "neutral").lower()] += 1
        kol_counts[target.name] += 1

    # Combine trend + engagement into final score
    def _score(topic: str) -> float:
        return (
            topic_trend.get(topic, 0.0)
            + topic_likes.get(topic, 0) * 0.001
            + topic_views.get(topic, 0) * 0.0001
        )

    sorted_topics = sorted(topic_counts.keys(), key=_score, reverse=True)[:10]

    return {
        "period_days": days,
        "total": len(insights),
        "categories": [
            {"name": k, "count": v}
            for k, v in cat_counts.most_common(8)
        ],
        "top_topics": [
            {
                "topic": t,
                "count": topic_counts[t],
                "trend_score": round(_score(t), 3),
                "likes": topic_likes.get(t, 0),
                "views": topic_views.get(t, 0),
                "url": topic_urls.get(t),
            }
            for t in sorted_topics
        ],
        "sentiment": [
            {"name": k.capitalize(), "count": v}
            for k, v in sentiment_counts.most_common()
        ],
        "top_kols": [
            {"name": k, "count": v}
            for k, v in kol_counts.most_common(10)
        ],
    }


@app.get("/api/stats")
async def stats():
    from app.database import AsyncSessionLocal
    from app.models import Target, ExtractedInsight, RunLog, RunStatus
    from sqlalchemy import select, func, desc
    from datetime import datetime, timezone

    # Use UTC midnight so insights stored as UTC timestamps are counted correctly
    # regardless of the server's local timezone.
    now_utc = datetime.now(timezone.utc)
    today_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    async with AsyncSessionLocal() as sess:
        active_targets = await sess.execute(select(func.count()).select_from(Target).where(Target.active == True))
        # AE-derived insights are hidden everywhere — keep the dashboard
        # counters consistent with what's actually visible.
        total_insights = await sess.execute(
            select(func.count()).select_from(ExtractedInsight).where(insight_not_ae())
        )
        today_insights = await sess.execute(
            select(func.count()).select_from(ExtractedInsight)
            .where(ExtractedInsight.extracted_at >= today_start_utc)
            .where(insight_not_ae())
        )
        last_run = await sess.execute(
            select(RunLog).order_by(desc(RunLog.started_at)).limit(1)
        )
        last = last_run.scalar_one_or_none()

    return {
        "active_targets": active_targets.scalar(),
        "total_insights": total_insights.scalar(),
        "today_insights": today_insights.scalar(),
        "last_run_at": last.started_at.isoformat() if last else None,
        "last_run_status": last.status if last else None,
    }


def _extract_brief_strings(raw: str) -> list[str]:
    """Extract the list of brief points from an LLM response.

    The prompts ask for a JSON array of strings, so try that first (most
    reliable). Fall back to quoted-sentence regex only if JSON parsing fails
    (e.g. the array was truncated mid-stream)."""
    import re as _re, json as _json

    # 1) Proper JSON array parse — handles any punctuation / count correctly.
    cleaned = _re.sub(r'```(?:json)?|```', '', raw).strip()
    m = _re.search(r'\[.*\]', cleaned, _re.DOTALL)
    if m:
        try:
            arr = _json.loads(m.group(0))
            out = [str(s).strip() for s in arr if isinstance(s, str) and s.strip()]
            if out:
                return out
        except Exception:
            pass

    # 2) Split on ITEM BOUNDARIES, not on quotes.
    #
    # The old fallback matched quoted runs, which shatters a string containing
    # an unescaped inner quote — a model writing  Roche's "Alecensa" in NSCLC
    # yielded the fragment ' in NSCLC...' as if it were a whole finding, and it
    # rendered that way on the dashboard. A real item boundary is  ", "  so
    # split on that instead and tolerate quotes inside an item.
    if m:
        body = m.group(0).strip()[1:-1].strip()          # drop the [ ]
        parts = _re.split(r'"\s*,\s*"', body)
        if parts:
            out = []
            for part in parts:
                text = part.strip().strip('"').strip()
                # Unescape what json.loads would have handled.
                text = text.replace('\\"', '"').replace("\\n", " ").strip()
                if len(text) >= 20:
                    out.append(text)
            if out:
                return out

    # 3) Last resort: quoted sentences. Fragments are dropped — a point that
    #    starts mid-sentence is noise, and showing it as a finding is worse
    #    than showing one point fewer.
    strings = _re.findall(r'"((?:[^"\\]|\\.)+[.!?])"', raw)
    if not strings:
        strings = [s for s in _re.findall(r'"((?:[^"\\]|\\.){20,})"', raw) if not s.startswith("http")]
    return [s.strip() for s in strings if s.strip() and s.strip()[0].isupper()]


def _brief_priority(s: str) -> str:
    roche_terms = {"roche","tecentriq","atezolizumab","alecensa","alectinib","perjeta","herceptin","avastin","kadcyla","polivy","hemlibra","ocrevus","vabysmo"}
    flags = ["competitor","unmet","concern","critical","negative","threat","gap","emerging"]
    return "high" if any(t in s.lower() for t in roche_terms) or any(w in s.lower() for w in flags) else "medium"


@app.get("/api/stats/daily-brief")
async def daily_brief(refresh: bool = False, user=Depends(daily_gen_guard("daily_brief"))):
    """Combined KOL + Social brief — 30-day data window. Cached 6h."""
    import json as _json, re as _re
    from datetime import datetime, timezone, timedelta

    _KEY = "combined_brief:v4"
    _UKEY = f"{_KEY}:u{user.id}"
    r = None
    try:
        import redis as _redis
        from app.config import get_settings as _gs
        r = _redis.Redis.from_url(_gs().redis_url, socket_timeout=2)
        if not refresh:
            cached = r.get(_UKEY) or r.get(_KEY)
            if cached:
                result = _json.loads(cached)
                if isinstance(result, dict):
                    result["cached"] = True
                return result
        # on refresh: skip the cache and regenerate into the per-user key (below)
    except Exception:
        r = None

    from app.database import AsyncSessionLocal
    from app.models import ExtractedInsight, Target, SocialPost
    from sqlalchemy import select, desc

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=BRIEF_WINDOW_DAYS)

    async with AsyncSessionLocal() as sess:
        ins_rows = await sess.execute(
            select(ExtractedInsight, Target.name)
            .join(Target, ExtractedInsight.target_id == Target.id)
            .where(ExtractedInsight.extracted_at >= window_start)
            .where(insight_not_ae())
            .order_by(desc(ExtractedInsight.extracted_at))
            .limit(60)
        )
        insights = ins_rows.all()

        social_rows = await sess.execute(
            select(SocialPost)
            .where(SocialPost.scraped_at >= window_start)
            .where(social_not_ae())
            .order_by(desc(SocialPost.likes + SocialPost.comments * 2))
            .limit(20)
        )
        social_posts = social_rows.scalars().all()

    if not insights and not social_posts:
        return {"points": [], "generated_at": None, "cached": False, "kol_count": 0, "social_count": 0, "error": None}

    insights_text = "\n".join(
        f"- KOL:{name} | topic:{ins.topic} | sentiment:{ins.sentiment or 'neutral'} | said:\"{(ins.what_they_said or '')[:200]}\""
        for ins, name in insights
    ) or "No KOL insights."

    social_text = "\n".join(
        f"- [{p.platform},{p.likes}likes] topic:{p.topic} | \"{(p.text or '')[:120]}\""
        for p in social_posts
    ) or "No social posts."

    from app.services.llm_router import call_llm_async, once_only
    import structlog as _sl
    _log = _sl.get_logger("combined_brief")

    prompt = (
        "You are a senior pharma intelligence analyst for Roche's oncology strategy team.\n\n"
        "Below are real KOL statements and top social media posts from the last 30 days.\n"
        "Generate sharp, SPECIFIC intelligence points combining both KOL and social signals — "
        "5 to 8 points (never fewer than 5), ordered most important first. Cover "
        "DISTINCT angles — do not restate one finding several ways.\n\n"
        "Rules:\n"
        "- Every point must matter to Roche France specifically — its drugs, its competitors, "
        "or its oncology strategy in France\n"
        "- Mention actual drug names, KOL names, or specific data when available\n"
        "- Each point must be actionable: what should Roche France watch, do, or address?\n"
        "- Flag competitive threats or unmet needs explicitly\n"
        "- Do NOT write generic statements — trace every point back to the data\n"
        "- Each point max 30 words\n"
        "- You MUST return at least 5 points; if signals are genuinely sparse, "
        "cover more of the material rather than repeating a point\n\n"
        f"KOL STATEMENTS ({len(insights)}):\n{insights_text}\n\n"
        f"TOP SOCIAL POSTS ({len(social_posts)}):\n{social_text}\n\n"
        "Return ONLY a JSON array of at least 3 (up to 5) strings. No markdown:\n"
        '["point 1", "point 2", "point 3"]'
    )

    llm_error = None
    points = []
    try:
        # gemini-2.5-flash counts REASONING against this budget, and these prompts
        # carry the whole insight corpus (~22k chars for 60 insights). Measured at
        # 2048: the JSON array was cut mid-string, parsing failed, and the regex
        # fallback salvaged only the first CLOSED quote — one point from sixty
        # insights. At 8192 the same prompt returns five.
        # Two clicks on Regenerate ran this twice — same prompt, same corpus,
        # double the cost, and the loser discarded by last-write-wins. The
        # second caller now awaits the first instead.
        raw = await once_only(
            "brief:daily-brief",
            lambda: call_llm_async([{"role": "user", "content": prompt}], max_tokens=8192))
        _log.info("combined_brief.llm_raw", raw=raw[:400])
        strings = _extract_brief_strings(raw)
        points = [{"text": s, "source": "both", "priority": _brief_priority(s)} for s in strings[:10]]
        if not points:
            llm_error = f"No strings extracted: {raw[:200]}"
    except Exception as exc:
        llm_error = str(exc)[:300]
        _log.warning("combined_brief.failed", exc=llm_error)

    result = {
        "points": points,
        "generated_at": now.isoformat(),
        "cached": False,
        "kol_count": len(insights),
        "social_count": len(social_posts),
        "error": llm_error,
    }

    # Only cache if we got actual points
    try:
        if r and points:
            r.set(_UKEY if refresh else _KEY, _json.dumps(result), ex=21600)
    except Exception:
        pass

    return result


class BriefDetailRequest(BaseModel):
    point: str


@app.post("/api/stats/brief-detail")
async def brief_detail(body: BriefDetailRequest, user=Depends(get_current_user)):
    """Expand a brief point into full detail: KOL evidence, social evidence, so-what, links."""
    from datetime import datetime, timezone, timedelta
    import json as _json

    point_text = body.point.strip()
    if not point_text:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="point required")

    from app.database import AsyncSessionLocal
    from app.models import ExtractedInsight, Target, SocialPost
    from app.models.discovery_result import DiscoveryResult
    from sqlalchemy import select, desc, or_, func

    # Extract keywords from the point (words > 4 chars)
    import re as _re
    keywords = [w.lower() for w in _re.findall(r'\b[a-zA-Z]{4,}\b', point_text)][:8]

    async with AsyncSessionLocal() as sess:
        # Find matching KOL insights
        kol_rows = await sess.execute(
            select(ExtractedInsight, Target.name)
            .join(Target, ExtractedInsight.target_id == Target.id)
            .where(or_(
                *[func.lower(ExtractedInsight.topic).contains(kw) for kw in keywords[:4]],
                *[func.lower(ExtractedInsight.what_they_said).contains(kw) for kw in keywords[:4]],
            ))
            .where(insight_not_ae())
            .order_by(desc(ExtractedInsight.extracted_at))
            .limit(8)
        )
        kol_insights = kol_rows.all()

        # Find matching social posts
        social_rows = await sess.execute(
            select(SocialPost)
            .where(or_(
                *[func.lower(SocialPost.text).contains(kw) for kw in keywords[:4]],
                *[func.lower(SocialPost.topic).contains(kw) for kw in keywords[:4]],
            ))
            .where(social_not_ae())
            .order_by(desc(SocialPost.likes + SocialPost.comments * 2))
            .limit(6)
        )
        social_posts = social_rows.scalars().all()

        # Find relevant discovery links
        link_rows = await sess.execute(
            select(DiscoveryResult.url, DiscoveryResult.title, DiscoveryResult.source_name)
            .where(or_(
                *[func.lower(DiscoveryResult.snippet).contains(kw) for kw in keywords[:3]],
                *[func.lower(DiscoveryResult.title).contains(kw) for kw in keywords[:3]],
            ))
            .order_by(desc(DiscoveryResult.scraped_at))
            .limit(5)
        )
        links = [{"url": r.url, "title": r.title or r.source_name or r.url} for r in link_rows]

    kol_text = "\n".join(
        f"- {name} ({ins.sentiment or 'neutral'}): {(ins.what_they_said or '')[:200]}"
        for ins, name in kol_insights
    ) or "No matching KOL insights."

    social_text = "\n".join(
        f"- [{p.platform}, {p.likes}likes] {(p.text or '')[:150]}"
        for p in social_posts
    ) or "No matching social posts."

    from app.services.llm_router import call_llm_async, once_only
    import re as _re2

    def _extract_sec(text: str, marker: str) -> str:
        m = _re2.search(rf'##{marker}##\s*(.*?)(?=##[A-Z_]+##|$)', text, _re2.DOTALL | _re2.IGNORECASE)
        return m.group(1).strip() if m else ""

    prompt = (
        f"You are a pharma intelligence analyst for Roche.\n\n"
        f"INTELLIGENCE POINT: {point_text}\n\n"
        f"KOL EVIDENCE:\n{kol_text}\n\n"
        f"SOCIAL EVIDENCE:\n{social_text}\n\n"
        "Write a detailed pharma intelligence briefing using EXACTLY these section markers:\n\n"
        "##SUMMARY##\n"
        "Write 5-15 sentences: what KOLs said, which drugs/trials are involved, sentiment, "
        "supporting social signals, and any competitive implications.\n\n"
        "##SO_WHAT##\n"
        "Write 3-5 sentences: specific impact on Roche — pipeline, competitive position, "
        "strategic opportunity or threat.\n\n"
        "##ACTION##\n"
        "Write 2-3 concrete actions Roche should take with timelines."
    )

    detail = {}
    try:
        raw = await call_llm_async([{"role": "user", "content": prompt}], max_tokens=8192)
        detail = {
            "summary": _extract_sec(raw, "SUMMARY") or point_text,
            "so_what": _extract_sec(raw, "SO_WHAT"),
            "action":  _extract_sec(raw, "ACTION"),
        }
    except Exception as exc:
        detail = {"summary": point_text, "so_what": "", "action": ""}

    return {
        "point": point_text,
        "summary": detail.get("summary", point_text),
        "so_what": detail.get("so_what", ""),
        "action": detail.get("action", ""),
        "kol_insights": [
            {"kol": name, "topic": ins.topic, "said": (ins.what_they_said or "")[:300], "sentiment": ins.sentiment}
            for ins, name in kol_insights
        ],
        "social_posts": [
            {"platform": p.platform, "text": (p.text or "")[:200], "likes": p.likes, "url": p.post_url}
            for p in social_posts
        ],
        "links": links,
    }


@app.get("/api/stats/social-brief")
async def social_brief(refresh: bool = False, user=Depends(daily_gen_guard("social_brief"))):
    """Sector-grouped social trends brief — 200 posts, 30-day window."""
    import json as _json, re as _re
    from datetime import datetime, timezone, timedelta
    from collections import defaultdict

    _KEY = "social_brief:v4"
    _UKEY = f"{_KEY}:u{user.id}"
    r = None
    try:
        import redis as _redis
        from app.config import get_settings as _gs
        r = _redis.Redis.from_url(_gs().redis_url, socket_timeout=2)
        if not refresh:
            cached = r.get(_UKEY) or r.get(_KEY)
            if cached:
                return _json.loads(cached)
        # on refresh: skip the cache and regenerate into the per-user key (below)
    except Exception:
        r = None

    from app.database import AsyncSessionLocal
    from app.models import SocialPost
    from sqlalchemy import select, desc

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=BRIEF_WINDOW_DAYS)

    async with AsyncSessionLocal() as sess:
        rows = await sess.execute(
            select(SocialPost)
            .where(SocialPost.scraped_at >= window_start)
            .where(social_not_ae())
            .order_by(desc(SocialPost.likes + SocialPost.comments * 2 + SocialPost.shares * 1.5))
            .limit(200)
        )
        posts = rows.scalars().all()

    if not posts:
        return {"sections": [], "total_posts": 0, "generated_at": None, "cached": False, "error": None}

    # Build topic engagement stats
    topic_stats: dict = defaultdict(lambda: {"count": 0, "likes": 0, "comments": 0, "platforms": set()})
    for p in posts:
        t = p.topic or p.query or "other"
        topic_stats[t]["count"] += 1
        topic_stats[t]["likes"] += p.likes or 0
        topic_stats[t]["comments"] += p.comments or 0
        topic_stats[t]["platforms"].add(p.platform)

    top_topics = sorted(topic_stats.items(), key=lambda x: x[1]["likes"] + x[1]["comments"] * 2, reverse=True)[:12]
    topics_detail = "\n".join(
        f"- topic:{t} | posts:{s['count']} | likes:{s['likes']} | comments:{s['comments']} | platforms:{','.join(s['platforms'])}"
        for t, s in top_topics
    )

    # Sample posts per top topic
    topic_set = {t for t, _ in top_topics}
    posts_sample = "\n".join(
        f"- [{p.platform},{p.likes}♥,{p.comments}💬] topic:{p.topic or p.query} | \"{(p.text or '')[:180]}\""
        for p in posts[:80] if (p.topic or p.query) in topic_set
    )

    from app.services.llm_router import call_llm_async, once_only
    import structlog as _sl
    _log = _sl.get_logger("social_brief")

    prompt = (
        "You are a senior pharma social media intelligence analyst for Roche.\n\n"
        f"Analyzed {len(posts)} posts from Instagram, X, LinkedIn, Facebook over the last 30 days.\n\n"
        f"TOP TOPICS BY ENGAGEMENT:\n{topics_detail}\n\n"
        f"SAMPLE POSTS:\n{posts_sample}\n\n"
        "Generate a structured intelligence report organized into 4-5 SECTORS "
        "(e.g. 'Oncology Treatments', 'Clinical Trials & Data', 'Competitive Landscape', "
        "'Patient Community', 'Regulatory & Policy', 'Conferences & Events').\n\n"
        "For each sector provide 2-3 specific intelligence points.\n"
        "Rules:\n"
        "- Reference actual drug names, hashtags, or platforms from the data\n"
        "- Each point must be actionable for Roche: what to monitor, respond to, or leverage\n"
        "- Flag engagement spikes, sentiment shifts, or emerging competitor mentions\n"
        "- Each point max 35 words\n\n"
        "Return ONLY this JSON structure, no markdown:\n"
        '{"sections": [{"sector": "sector name", "key_signal": "one-line summary", '
        '"points": ["point 1", "point 2", "point 3"]}]}'
    )

    llm_error = None
    sections = []
    try:
        # gemini-2.5-flash counts REASONING against this budget, and these prompts
        # carry the whole insight corpus (~22k chars for 60 insights). Measured at
        # 2048: the JSON array was cut mid-string, parsing failed, and the regex
        # fallback salvaged only the first CLOSED quote — one point from sixty
        # insights. At 8192 the same prompt returns five.
        # Two clicks on Regenerate ran this twice — same prompt, same corpus,
        # double the cost, and the loser discarded by last-write-wins. The
        # second caller now awaits the first instead.
        raw = await once_only(
            "brief:social-brief",
            lambda: call_llm_async([{"role": "user", "content": prompt}], max_tokens=8192))
        _log.info("social_brief.llm_raw", raw=raw[:500])
        raw_clean = _re.sub(r'```(?:json)?\s*|\s*```', '', raw).strip()
        m = _re.search(r'\{.*\}', raw_clean, _re.DOTALL)
        if m:
            parsed = _json.loads(m.group(0))
            raw_sections = parsed.get("sections", [])
            for sec in raw_sections:
                pts = sec.get("points", [])
                sections.append({
                    "sector": sec.get("sector", "General"),
                    "key_signal": sec.get("key_signal", ""),
                    "points": [{"text": p, "source": "social", "priority": _brief_priority(p)} for p in pts if isinstance(p, str)],
                })
        if not sections:
            # Fallback: extract strings
            strings = _extract_brief_strings(raw)
            if strings:
                sections = [{"sector": "Social Trends", "key_signal": "", "points": [{"text": s, "source": "social", "priority": "medium"} for s in strings[:10]]}]
            else:
                llm_error = f"Parse failed: {raw[:200]}"
    except Exception as exc:
        llm_error = str(exc)[:300]
        _log.warning("social_brief.failed", exc=llm_error)

    result = {
        "sections": sections,
        "points": [p for sec in sections for p in sec["points"]],  # flat list for compatibility
        "total_posts": len(posts),
        "top_topics": [{"topic": t, "count": s["count"], "engagement": s["likes"] + s["comments"] * 2} for t, s in top_topics[:8]],
        "generated_at": now.isoformat(),
        "cached": False,
        "error": llm_error,
    }
    try:
        if r and sections:
            r.set(_UKEY if refresh else _KEY, _json.dumps(result), ex=21600)
    except Exception:
        pass
    return result


@app.get("/api/stats/kol-brief")
async def kol_brief(refresh: bool = False, user=Depends(daily_gen_guard("kol_brief"))):
    """KOL-only brief — 30-day insights window. Cached 6h."""
    import json as _json, re as _re
    from datetime import datetime, timezone, timedelta

    _KEY = "kol_brief:v4"
    _UKEY = f"{_KEY}:u{user.id}"
    r = None
    try:
        import redis as _redis
        from app.config import get_settings as _gs
        r = _redis.Redis.from_url(_gs().redis_url, socket_timeout=2)
        if not refresh:
            cached = r.get(_UKEY) or r.get(_KEY)
            if cached:
                return _json.loads(cached)
        # on refresh: skip the cache and regenerate into the per-user key (below)
    except Exception:
        r = None

    from app.database import AsyncSessionLocal
    from app.models import ExtractedInsight, Target
    from sqlalchemy import select, desc

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=BRIEF_WINDOW_DAYS)

    async with AsyncSessionLocal() as sess:
        ins_rows = await sess.execute(
            select(ExtractedInsight, Target.name)
            .join(Target, ExtractedInsight.target_id == Target.id)
            .where(ExtractedInsight.extracted_at >= window_start)
            .where(insight_not_ae())
            .where(Target.target_type == "kol")   # competitor content must NOT bleed into the KOL brief
            .order_by(desc(ExtractedInsight.extracted_at))
            .limit(60)
        )
        insights = ins_rows.all()

    if not insights:
        return {"points": [], "generated_at": None, "cached": False, "kol_count": 0, "social_count": 0, "error": None}

    insights_text = "\n".join(
        f"- KOL:{name} | topic:{ins.topic} | sentiment:{ins.sentiment or 'neutral'} | category:{ins.category or ''} | said:\"{(ins.what_they_said or '')[:200]}\""
        for ins, name in insights
    )

    from app.services.llm_router import call_llm_async, once_only
    import structlog as _sl
    _log = _sl.get_logger("kol_brief")

    prompt = (
        "You are a senior pharma intelligence analyst for Roche's oncology strategy team.\n\n"
        f"Below are {len(insights)} real KOL statements from the last 30 days.\n"
        "Generate sharp, SPECIFIC intelligence points based ONLY on what these KOLs said — "
        "5 to 8 points (never fewer than 5), ordered most important first. Cover "
        "DISTINCT angles — do not restate one finding several ways.\n\n"
        "Rules:\n"
        "- Every point must matter to Roche France specifically — its drugs, its competitors, "
        "or its oncology strategy in France\n"
        "- Quote actual KOL names and drug names from the data\n"
        "- Every point must be actionable for Roche France's strategy\n"
        "- Flag competitive threats, unmet needs, or sentiment shifts explicitly\n"
        "- Do NOT write generic statements — trace every point back to a specific KOL\n"
        "- Each point max 30 words\n"
        "- You MUST return at least 5 points; if signals are genuinely sparse, "
        "cover more of the material rather than repeating a point\n\n"
        f"KOL STATEMENTS:\n{insights_text}\n\n"
        "Return ONLY a JSON array of at least 3 (up to 5) strings. No markdown:\n"
        '["point 1", "point 2", "point 3"]'
    )

    llm_error = None
    points = []
    try:
        # gemini-2.5-flash counts REASONING against this budget, and these prompts
        # carry the whole insight corpus (~22k chars for 60 insights). Measured at
        # 2048: the JSON array was cut mid-string, parsing failed, and the regex
        # fallback salvaged only the first CLOSED quote — one point from sixty
        # insights. At 8192 the same prompt returns five.
        # Two clicks on Regenerate ran this twice — same prompt, same corpus,
        # double the cost, and the loser discarded by last-write-wins. The
        # second caller now awaits the first instead.
        raw = await once_only(
            "brief:kol-brief",
            lambda: call_llm_async([{"role": "user", "content": prompt}], max_tokens=8192))
        _log.info("kol_brief.llm_raw", raw=raw[:400])
        strings = _extract_brief_strings(raw)
        points = [{"text": s, "source": "kol", "priority": _brief_priority(s)} for s in strings[:10]]
        if not points:
            llm_error = f"No strings extracted: {raw[:200]}"
    except Exception as exc:
        llm_error = str(exc)[:300]
        _log.warning("kol_brief.failed", exc=llm_error)

    result = {
        "points": points,
        "generated_at": now.isoformat(),
        "cached": False,
        "kol_count": len(insights),
        "social_count": 0,
        "error": llm_error,
    }
    try:
        if r and points:
            r.set(_UKEY if refresh else _KEY, _json.dumps(result), ex=21600)
    except Exception:
        pass
    return result


@app.get("/api/stats/competitor-brief")
async def competitor_brief(refresh: bool = False, user=Depends(daily_gen_guard("competitor_brief"))):
    """Competitor-only brief — same mechanism as the KOL brief, scoped to
    target_type='competitor'. Cached 6h."""
    import json as _json
    from datetime import datetime, timezone, timedelta

    _KEY = "competitor_brief:v2"
    _UKEY = f"{_KEY}:u{user.id}"
    r = None
    try:
        import redis as _redis
        from app.config import get_settings as _gs
        r = _redis.Redis.from_url(_gs().redis_url, socket_timeout=2)
        if not refresh:
            cached = r.get(_UKEY) or r.get(_KEY)
            if cached:
                result = _json.loads(cached)
                if isinstance(result, dict):
                    result["cached"] = True
                return result
    except Exception:
        r = None

    from app.database import AsyncSessionLocal
    from app.models import ExtractedInsight, Target
    from sqlalchemy import select, desc

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=BRIEF_WINDOW_DAYS)

    async with AsyncSessionLocal() as sess:
        ins_rows = await sess.execute(
            select(ExtractedInsight, Target.name)
            .join(Target, ExtractedInsight.target_id == Target.id)
            .where(ExtractedInsight.extracted_at >= window_start)
            .where(insight_not_ae())
            .where(Target.target_type == "competitor")
            .order_by(desc(ExtractedInsight.extracted_at))
            .limit(60)
        )
        insights = ins_rows.all()

    if not insights:
        return {"points": [], "generated_at": None, "cached": False, "kol_count": 0,
                "social_count": 0,
                "error": "No competitor insights yet — add competitor targets and run a scrape."}

    insights_text = "\n".join(
        f"- COMPETITOR:{name} | topic:{ins.topic} | sentiment:{ins.sentiment or 'neutral'} | "
        f"category:{ins.category or ''} | said:\"{(ins.what_they_said or '')[:200]}\""
        for ins, name in insights
    )

    from app.services.llm_router import call_llm_async, once_only
    import structlog as _sl
    _log = _sl.get_logger("competitor_brief")

    prompt = (
        "You are a senior competitive-intelligence analyst for Roche's oncology strategy team.\n\n"
        f"Below are {len(insights)} statements/publications from monitored COMPETITOR accounts "
        "(rival pharma companies) over the last 30 days.\n"
        "Generate sharp, SPECIFIC competitive-intelligence points — 5 to 8 points, "
        "ordered most important first, each on a DISTINCT angle "
        "(never fewer than 3), the MOST important ones only.\n\n"
        "Rules:\n"
        "- Focus on what competitors are launching, claiming, trialing, or signalling\n"
        "- Name the competitor company and drug/trial explicitly\n"
        "- Say what each move means for Roche France and what to watch or counter\n"
        "- Do NOT write generic statements — trace every point back to the data\n"
        "- Each point max 30 words\n\n"
        f"COMPETITOR STATEMENTS:\n{insights_text}\n\n"
        "Return ONLY a JSON array of at least 3 (up to 5) strings. No markdown:\n"
        '["point 1", "point 2", "point 3"]'
    )

    llm_error = None
    points = []
    try:
        # gemini-2.5-flash counts REASONING against this budget, and these prompts
        # carry the whole insight corpus (~22k chars for 60 insights). Measured at
        # 2048: the JSON array was cut mid-string, parsing failed, and the regex
        # fallback salvaged only the first CLOSED quote — one point from sixty
        # insights. At 8192 the same prompt returns five.
        # Two clicks on Regenerate ran this twice — same prompt, same corpus,
        # double the cost, and the loser discarded by last-write-wins. The
        # second caller now awaits the first instead.
        raw = await once_only(
            "brief:competitor-brief",
            lambda: call_llm_async([{"role": "user", "content": prompt}], max_tokens=8192))
        _log.info("competitor_brief.llm_raw", raw=raw[:400])
        strings = _extract_brief_strings(raw)
        points = [{"text": s, "source": "competitor", "priority": _brief_priority(s)} for s in strings[:10]]
        if not points:
            llm_error = f"No strings extracted: {raw[:200]}"
    except Exception as exc:
        llm_error = str(exc)[:300]
        _log.warning("competitor_brief.failed", exc=llm_error)

    result = {
        "points": points,
        "generated_at": now.isoformat(),
        "cached": False,
        "kol_count": len(insights),
        "social_count": 0,
        "error": llm_error,
    }
    try:
        if r and points:
            r.set(_UKEY if refresh else _KEY, _json.dumps(result), ex=21600)
    except Exception:
        pass
    return result


@app.get("/api/stats/competitor-report")
async def competitor_report(refresh: bool = False, window_days: int = 180,
                            user=Depends(daily_gen_guard("competitor_report"))):
    """Competitor intelligence in the same 6-section market-research format as
    Topic Explorer, Burning Topics and Account Tracking — Executive Summary,
    So What, What is being said, Voice distribution, Volume of mentions, Key
    sub-topics. The flat-bullet /competitor-brief above stays as-is for the
    Dashboard's compact card; this is the full report for the Competitors page,
    which is where the client asked for the structured deliverable.
    """
    import json as _json
    from datetime import datetime, timezone

    from app.services.market_report import build_prompt, gather_competitors, parse_report

    _KEY = f"competitor_report:v1:{window_days}"
    _UKEY = f"{_KEY}:u{user.id}"
    r = None
    try:
        import redis as _redis
        from app.config import get_settings as _gs
        r = _redis.Redis.from_url(_gs().redis_url, socket_timeout=2)
        if not refresh:
            cached = r.get(_UKEY) or r.get(_KEY)
            if cached:
                result = _json.loads(cached)
                if isinstance(result, dict):
                    result["cached"] = True
                return result
    except Exception:
        r = None

    from app.database import AsyncSessionLocal

    now = datetime.now(timezone.utc)
    question = ("What are rival pharma companies launching, claiming, and "
               "signalling in oncology that Roche France should track?")

    async with AsyncSessionLocal() as sess:
        material = await gather_competitors(sess, window_days=window_days)

    base = {
        "question": question, "generated_at": now.isoformat(),
        "window_days": window_days, "item_count": material.total, "cached": False,
    }

    if not material.items:
        empty = parse_report("", material)
        return {
            **base, **empty,
            "error": "No competitor insights yet — add competitor targets and run a scrape.",
        }

    from app.services.llm_router import call_llm_async, once_only
    import structlog as _sl
    _log = _sl.get_logger("competitor_report")

    llm_error = None
    try:
        raw = await once_only(
            "competitor-report",
            lambda: call_llm_async([{"role": "user", "content": build_prompt(question, material)}],
                                   max_tokens=8192))
        _log.info("competitor_report.llm_raw", raw=raw[:400])
        report = parse_report(raw, material)
    except Exception as exc:
        llm_error = str(exc)[:300]
        _log.warning("competitor_report.failed", exc=llm_error)
        report = parse_report("", material)

    result = {**base, **report, "error": llm_error}
    try:
        if r and not llm_error:
            r.set(_UKEY if refresh else _KEY, _json.dumps(result), ex=21600)
    except Exception:
        pass
    return result


@app.get("/api/stats/competitor-publications")
async def competitor_publications(days: int = 90, limit: int = 20, user=Depends(get_current_user)):
    """Top competitor publications ranked by engagement.

    Engagement fields that actually exist on scraped posts: `likes` and `views`
    (nullable — captured only where the source page exposed them; no
    comments/shares column exists). Ranked by likes+views, recency as
    tiebreak/fallback where engagement is missing."""
    from datetime import datetime, timezone, timedelta
    from app.database import AsyncSessionLocal
    from app.models import ScrapedPost, Target
    from sqlalchemy import select, desc, func

    since = datetime.now(timezone.utc) - timedelta(days=days)
    engagement = func.coalesce(ScrapedPost.likes, 0) + func.coalesce(ScrapedPost.views, 0)

    async with AsyncSessionLocal() as sess:
        rows = await sess.execute(
            select(ScrapedPost, Target.name, engagement.label("engagement"))
            .join(Target, ScrapedPost.target_id == Target.id)
            .where(Target.target_type == "competitor")
            .where(ScrapedPost.scraped_at >= since)
            .where(post_not_ae())
            .order_by(desc("engagement"), desc(ScrapedPost.scraped_at))
            .limit(max(1, min(limit, 100)))
        )
        results = rows.all()

    return {
        "period_days": days,
        "total": len(results),
        "publications": [
            {
                "id": post.id,
                "competitor": name,
                "title": post.title,
                "url": post.source_url,
                "source": post.source_name or post.source_type or "web",
                "published_date": post.published_date,
                "likes": post.likes or 0,
                "views": post.views or 0,
                "engagement": int(eng or 0),
                "excerpt": (post.raw_content or "")[:280],
            }
            for post, name, eng in results
        ],
    }


@app.get("/api/stats/synthesis")
async def combined_synthesis(refresh: bool = False, user=Depends(daily_gen_guard("synthesis"))):
    """Holistic AI synthesis over the WHOLE database — KOL insights + social posts.

    Always produces output as long as there's any data (does not require social
    posts). Returns: what's happening, so what for Roche, the bottom-line
    conclusion, and a short 'focus' list. Cached 6h.
    """
    import json as _json
    from datetime import datetime, timezone, timedelta

    _KEY = "combined_synth:v1"
    _UKEY = f"{_KEY}:u{user.id}"
    r = None
    try:
        import redis as _redis
        from app.config import get_settings as _gs
        r = _redis.Redis.from_url(_gs().redis_url, socket_timeout=2)
        if not refresh:
            cached = r.get(_UKEY) or r.get(_KEY)
            if cached:
                result = _json.loads(cached)
                if isinstance(result, dict):
                    result["cached"] = True
                return result
        # on refresh: skip the cache and regenerate into the per-user key (below)
    except Exception:
        r = None

    from app.database import AsyncSessionLocal
    from app.models import ExtractedInsight, Target, SocialPost
    from sqlalchemy import select, desc

    now = datetime.now(timezone.utc)
    wide = now - timedelta(days=365)

    async with AsyncSessionLocal() as sess:
        ins_rows = await sess.execute(
            select(ExtractedInsight, Target.name)
            .join(Target, ExtractedInsight.target_id == Target.id)
            .where(ExtractedInsight.extracted_at >= wide)
            .where(insight_not_ae())
            .order_by(desc(ExtractedInsight.extracted_at))
            .limit(80)
        )
        insights = ins_rows.all()
        # Fallback to all-time if the last year is empty
        if not insights:
            ins_rows = await sess.execute(
                select(ExtractedInsight, Target.name)
                .join(Target, ExtractedInsight.target_id == Target.id)
                .where(insight_not_ae())
                .order_by(desc(ExtractedInsight.extracted_at))
                .limit(80)
            )
            insights = ins_rows.all()

        social_rows = await sess.execute(
            select(SocialPost)
            .where(social_not_ae())
            .order_by(desc(SocialPost.likes + SocialPost.comments * 2 + SocialPost.shares * 1.5))
            .limit(30)
        )
        social_posts = social_rows.scalars().all()

    empty = {"takeaway": "", "so_what": "", "conclusion": "", "focus": [],
             "kol_count": 0, "social_count": 0, "generated_at": None, "cached": False, "error": None}
    if not insights and not social_posts:
        empty["error"] = "No data in the database yet — run the pipeline or a social scan first."
        return empty

    insights_text = "\n".join(
        f"- KOL:{name} | topic:{ins.topic} | sentiment:{ins.sentiment or 'neutral'} | "
        f"said:\"{(ins.what_they_said or '')[:200]}\""
        for ins, name in insights
    ) or "(no KOL insights)"
    social_text = "\n".join(
        f"- [{p.platform},{p.likes}likes] topic:{p.topic or p.query} | \"{(p.text or '')[:140]}\""
        for p in social_posts
    ) or "(no social posts)"

    from app.services.llm_router import call_llm_async, once_only
    from app.services.synthesizer import extract_section, parse_bullets, trim_incomplete
    import structlog as _sl
    _log = _sl.get_logger("combined_synth")

    prompt = (
        "You are the senior pharma intelligence lead for Roche France.\n"
        "Below is EVERYTHING currently in our intelligence database: monitored-KOL statements "
        "and the top social-media posts. Some sections may be empty — work with whatever data exists.\n\n"
        f"KOL STATEMENTS ({len(insights)}):\n{insights_text}\n\n"
        f"TOP SOCIAL POSTS ({len(social_posts)}):\n{social_text}\n\n"
        "Write a sharp executive synthesis. Use EXACTLY this format with these markers:\n"
        "##TAKEAWAY##\n"
        "3-5 sentences: what is happening right now across KOLs and social — themes, drug/competitor "
        "mentions, sentiment, notable shifts.\n"
        "##SO_WHAT##\n"
        "2-3 sentences: so what for Roche France — implications, threats, opportunities.\n"
        "##CONCLUSION##\n"
        "2-3 sentences: the bottom line — the single most important thing Roche should focus on now.\n"
        "##FOCUS##\n"
        "3-5 concrete focus items, one per line starting with '- '. Each actionable and specific.\n\n"
        "Reference real drug names, KOLs, hashtags. Never write generic filler — trace everything to the data."
    )

    err = None
    takeaway = so_what = conclusion = ""
    focus: list[str] = []
    try:
        raw = await call_llm_async([{"role": "user", "content": prompt}], max_tokens=8192)
        _log.info("combined_synth.llm_raw", raw=raw[:400])
        takeaway = trim_incomplete(extract_section(raw, "TAKEAWAY"))
        so_what = trim_incomplete(extract_section(raw, "SO_WHAT"))
        conclusion = trim_incomplete(extract_section(raw, "CONCLUSION"))
        focus = parse_bullets(extract_section(raw, "FOCUS"))[:6]
        if not takeaway and not focus:
            err = f"Parse failed: {raw[:200]}"
    except Exception as exc:
        err = str(exc)[:300]
        _log.warning("combined_synth.failed", exc=err)

    result = {
        "takeaway": takeaway,
        "so_what": so_what,
        "conclusion": conclusion,
        "focus": focus,
        "kol_count": len(insights),
        "social_count": len(social_posts),
        "generated_at": now.isoformat(),
        "cached": False,
        "error": err,
    }
    try:
        if r and (takeaway or focus):
            r.set(_UKEY if refresh else _KEY, _json.dumps(result), ex=21600)
    except Exception:
        pass
    return result


_GEN_FEATURES = [# Answering a typed question from the posts that matched it. Must match the
                 # daily_gen_guard key in routers/social.answer_question, or the
                 # quota check passes silently and the feature is unmetered.
                 "social_answer",
                 "daily_brief", "kol_brief", "social_brief", "synthesis",
                 "comparison_brief", "social_synthesis", "discovery_synthesis",
                 "competitor_brief", "competitor_report", "global_synthesis",
                 # The three downloadable dashboard syntheses. Quota keys must
                 # match the f"synthesis_{scope}" used in routers/reports.py.
                 "synthesis_kol", "synthesis_competitor", "synthesis_comprehensive",
                 # Ad-hoc Topic Explorer market-research report.
                 "market_report"]


@app.get("/api/me/gen-quota")
async def gen_quota(user=Depends(get_current_user)):
    """Per-feature: may this user still force a fresh AI regeneration today?
    Admins are always true. Frontend uses this to hide the regenerate button."""
    return {
        "admin": user.role == "admin",
        "features": {f: daily_generation_available(user, f) for f in _GEN_FEATURES},
    }


@app.get("/api/health/providers", dependencies=[Depends(require_admin)])
async def provider_health(refresh: bool = False):
    """Live health/usage for every configured API (LLM keys, scrapers, infra). Admin only."""
    from app.services.provider_health import get_provider_health
    return await get_provider_health(refresh=refresh)


@app.get("/api/stats/comparison-brief")
async def comparison_brief(refresh: bool = False, user=Depends(daily_gen_guard("comparison_brief"))):
    """Compare KOL signals vs social trends — alignment, gaps, strategic implications."""
    import json as _json, re as _re
    from datetime import datetime, timezone, timedelta

    _KEY = "comparison_brief:v1"
    _UKEY = f"{_KEY}:u{user.id}"
    r = None
    try:
        import redis as _redis
        from app.config import get_settings as _gs
        r = _redis.Redis.from_url(_gs().redis_url, socket_timeout=2)
        if not refresh:
            cached = r.get(_UKEY) or r.get(_KEY)
            if cached:
                return _json.loads(cached)
        # on refresh: skip the cache and regenerate into the per-user key (below)
    except Exception:
        r = None

    from app.database import AsyncSessionLocal
    from app.models import ExtractedInsight, Target, SocialPost
    from sqlalchemy import select, desc

    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as sess:
        ins_rows = await sess.execute(
            select(ExtractedInsight, Target.name)
            .join(Target, ExtractedInsight.target_id == Target.id)
            .where(insight_not_ae())
            .order_by(desc(ExtractedInsight.extracted_at))
            .limit(30)
        )
        insights = ins_rows.all()

        social_rows = await sess.execute(
            select(SocialPost)
            .where(SocialPost.scraped_at >= now - timedelta(days=30))
            .where(social_not_ae())
            .order_by(desc(SocialPost.likes + SocialPost.comments * 2))
            .limit(20)
        )
        social_posts = social_rows.scalars().all()

    if not insights and not social_posts:
        return {"points": [], "generated_at": None, "cached": False, "kol_count": 0, "social_count": 0, "error": None}

    kol_text = "\n".join(
        f"- {name}: topic={ins.topic}, sentiment={ins.sentiment or 'neutral'}, \"{(ins.what_they_said or '')[:150]}\""
        for ins, name in insights
    ) or "No KOL data."

    social_text = "\n".join(
        f"- [{p.platform},{p.likes}likes] topic={p.topic}, \"{(p.text or '')[:120]}\""
        for p in social_posts
    ) or "No social data."

    from app.services.llm_router import call_llm_async, once_only
    import structlog as _sl
    _log = _sl.get_logger("comparison_brief")

    prompt = (
        "You are a senior pharma intelligence analyst for Roche.\n\n"
        "Compare what KOLs (Key Opinion Leaders) are saying vs what is trending on social media.\n"
        "Generate 5 comparison intelligence points for Roche's strategy team.\n\n"
        "Focus on:\n"
        "- Topics where KOL views ALIGN with social trends (validation signal)\n"
        "- Topics where KOLs discuss something NOT yet trending socially (early signal)\n"
        "- Topics trending socially that KOLs have NOT addressed (gap or emerging issue)\n"
        "- Sentiment differences between KOLs and public social discourse\n"
        "- What Roche should prioritize given both signals together\n\n"
        "Each point max 30 words. Mention specific drugs, topics, or KOL names.\n\n"
        f"KOL INSIGHTS ({len(insights)}):\n{kol_text}\n\n"
        f"SOCIAL TRENDS ({len(social_posts)} posts):\n{social_text}\n\n"
        "Return ONLY a JSON array of 5 strings:\n"
        '["point 1", "point 2", "point 3", "point 4", "point 5"]'
    )

    llm_error = None
    points = []
    try:
        # gemini-2.5-flash counts REASONING against this budget, and these prompts
        # carry the whole insight corpus (~22k chars for 60 insights). Measured at
        # 2048: the JSON array was cut mid-string, parsing failed, and the regex
        # fallback salvaged only the first CLOSED quote — one point from sixty
        # insights. At 8192 the same prompt returns five.
        # Two clicks on Regenerate ran this twice — same prompt, same corpus,
        # double the cost, and the loser discarded by last-write-wins. The
        # second caller now awaits the first instead.
        raw = await once_only(
            "brief:comparison-brief",
            lambda: call_llm_async([{"role": "user", "content": prompt}], max_tokens=8192))
        _log.info("comparison_brief.llm_raw", raw=raw[:400])
        strings = _re.findall(r'"((?:[^"\\]|\\.)+[.!?])"', raw)
        if not strings:
            strings = [s for s in _re.findall(r'"((?:[^"\\]|\\.){20,})"', raw) if not s.startswith("http")]
        points = [{"text": s, "source": "both", "priority": "high"} for s in strings[:10]]
        if not points:
            llm_error = f"No strings extracted: {raw[:200]}"
    except Exception as exc:
        llm_error = str(exc)[:300]
        _log.warning("comparison_brief.llm_failed", exc=llm_error)

    result = {
        "points": points,
        "generated_at": now.isoformat(),
        "cached": False,
        "kol_count": len(insights),
        "social_count": len(social_posts),
        "error": llm_error,
    }
    try:
        if r and points:
            r.set(_UKEY if refresh else _KEY, _json.dumps(result), ex=21600)
    except Exception:
        pass
    return result


class SocialDetailRequest(BaseModel):
    point: str


@app.post("/api/stats/social-detail")
async def social_detail(body: SocialDetailRequest, user=Depends(get_current_user)):
    """Deep-dive on a social trend point — engagement stats, platform breakdown, pharma so-what."""
    import json as _json, re as _re
    from collections import defaultdict

    point_text = body.point.strip()
    keywords = [w.lower() for w in _re.findall(r'\b[a-zA-Z]{4,}\b', point_text)][:6]

    from app.database import AsyncSessionLocal
    from app.models import SocialPost
    from sqlalchemy import select, desc, or_, func as _func

    async with AsyncSessionLocal() as sess:
        rows = await sess.execute(
            select(SocialPost)
            .where(or_(
                *[_func.lower(SocialPost.text).contains(kw) for kw in keywords[:4]],
                *[_func.lower(SocialPost.topic).contains(kw) for kw in keywords[:3]],
            ))
            .where(social_not_ae())
            .order_by(desc(SocialPost.likes + SocialPost.comments * 2))
            .limit(20)
        )
        posts = rows.scalars().all()

    platform_stats: dict = defaultdict(lambda: {"count": 0, "likes": 0, "comments": 0})
    for p in posts:
        platform_stats[p.platform]["count"] += 1
        platform_stats[p.platform]["likes"] += p.likes or 0
        platform_stats[p.platform]["comments"] += p.comments or 0

    total_likes = sum(p.likes or 0 for p in posts)
    total_comments = sum(p.comments or 0 for p in posts)

    posts_text = "\n".join(
        f"- [{p.platform},{p.likes}♥,{p.comments}💬] \"{(p.text or '')[:220]}\" url:{p.post_url}"
        for p in posts[:12]
    ) or "No matching posts found."

    from app.services.llm_router import call_llm_async, once_only

    def _extract_section(text: str, marker: str) -> str:
        """Extract content between ##MARKER## and next ## or end."""
        m = _re.search(rf'##{marker}##\s*(.*?)(?=##[A-Z_]+##|$)', text, _re.DOTALL | _re.IGNORECASE)
        return m.group(1).strip() if m else ""

    prompt = (
        f"You are a senior pharma intelligence analyst for Roche.\n\n"
        f"SOCIAL TREND: {point_text}\n\n"
        f"MATCHING POSTS ({len(posts)} posts, {total_likes} total likes, {total_comments} comments):\n{posts_text}\n\n"
        "Write a detailed pharma intelligence briefing using EXACTLY these section markers:\n\n"
        "##SUMMARY##\n"
        "Write 5-15 sentences covering: what this trend is, who is posting about it, which drugs/hashtags/platforms are involved, engagement patterns, sentiment, and any competitive signals from the posts.\n\n"
        "##SO_WHAT##\n"
        "Write 3-5 sentences: specific implications for Roche — which pipeline products are affected, competitive threats, patient demand signals, partnership opportunities, or areas to monitor.\n\n"
        "##ACTION##\n"
        "Write 2-3 concrete actions Roche should take, with suggested timelines.\n\n"
        "##URGENCY##\n"
        "Write one word only: high, medium, or low\n\n"
        "##HASHTAGS##\n"
        "List the top 3-5 hashtags from the posts, comma separated."
    )

    detail: dict = {}
    try:
        raw = await call_llm_async([{"role": "user", "content": prompt}], max_tokens=8192)
        detail = {
            "summary":  _extract_section(raw, "SUMMARY") or point_text,
            "so_what":  _extract_section(raw, "SO_WHAT"),
            "action":   _extract_section(raw, "ACTION"),
            "urgency":  _extract_section(raw, "URGENCY").lower().strip().split()[0] if _extract_section(raw, "URGENCY") else "medium",
            "hashtags": [h.strip().lstrip("#") for h in _extract_section(raw, "HASHTAGS").split(",") if h.strip()],
        }
    except Exception as exc:
        detail = {"summary": point_text, "so_what": "", "action": "", "urgency": "medium", "hashtags": []}

    return {
        "point": point_text,
        "summary": detail.get("summary", point_text),
        "so_what": detail.get("so_what", ""),
        "action": detail.get("action", ""),
        "urgency": detail.get("urgency", "medium"),
        "hashtags": detail.get("hashtags", []),
        "total_likes": total_likes,
        "total_comments": total_comments,
        "platform_stats": dict(platform_stats),
        "posts": [
            {"platform": p.platform, "text": (p.text or "")[:250], "likes": p.likes or 0,
             "comments": p.comments or 0, "shares": p.shares or 0, "url": p.post_url,
             "topic": p.topic or p.query, "posted_at": p.posted_at.isoformat() if p.posted_at else None}
            for p in posts[:10]
        ],
    }


# ── SPA fallback ──────────────────────────────────────────

@app.get("/api/stats/share-of-voice")
async def share_of_voice(days: int = BRIEF_WINDOW_DAYS, source: str = "all",
                         user=Depends(get_current_user)):
    """Share of voice by product — Roche assets versus the competition.

    A brand lead thinks in assets, not topics: is the conversation about
    Tecentriq or Keytruda, and are we gaining or losing ground? Everything here
    is counted from text already stored — no extra scraping, no LLM call — so it
    is free to compute and refreshes as soon as a run lands.

    `source`: all | kol | social.
    """
    from datetime import datetime, timezone, timedelta

    from sqlalchemy import select, desc

    from app.database import AsyncSessionLocal
    from app.models import ExtractedInsight, ScrapedPost, SocialPost, Target
    from app.services.ae_filter import post_not_ae, social_not_ae
    from app.services.brands import BRANDS, tally

    window = max(1, min(days, 365))
    since = datetime.now(timezone.utc) - timedelta(days=window)
    items: list[dict] = []

    async with AsyncSessionLocal() as sess:
        if source in ("all", "kol"):
            rows = await sess.execute(
                select(ExtractedInsight, Target.name, ScrapedPost.domain)
                .join(Target, ExtractedInsight.target_id == Target.id)
                .join(ScrapedPost, ExtractedInsight.scraped_post_id == ScrapedPost.id)
                .where(ExtractedInsight.extracted_at >= since)
                .where(post_not_ae())
                .order_by(desc(ExtractedInsight.extracted_at))
                .limit(1000)
            )
            for insight, name, domain in rows.all():
                items.append({
                    # Topic carries the drug name as often as the quote does.
                    "text": f"{insight.topic or ''} {insight.what_they_said or ''}",
                    "sentiment": insight.sentiment,
                    "engagement": 0,
                    "source": name or domain or "",
                })

        if source in ("all", "social"):
            rows = await sess.execute(
                select(SocialPost)
                .where(SocialPost.scraped_at >= since)
                .where(social_not_ae())
                .order_by(desc(SocialPost.scraped_at))
                .limit(1000)
            )
            for post in rows.scalars().all():
                items.append({
                    "text": f"{post.topic or ''} {post.text or ''}",
                    "sentiment": None,     # social posts carry no rated sentiment
                    "engagement": (post.likes or 0) + 2 * (post.comments or 0),
                    "source": post.author or post.domain or post.platform or "",
                })

    result = tally(items)
    result.update({
        "window_days": window,
        "source": source,
        "items_scanned": len(items),
        "tracked_brands": len(BRANDS),
    })
    return result


_spa_dir = Path(__file__).parent.parent.parent / "frontend" / "dist"
if _spa_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(_spa_dir / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # Let real API 404s through — don't swallow them with the SPA shell
        if full_path.startswith("api/"):
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Not found")
        index = _spa_dir / "index.html"
        return FileResponse(str(index))
