import asyncio
from functools import partial

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AppSettings
from app.models.app_settings import PROVIDERS
from app.auth import require_admin

router = APIRouter(prefix="/api/settings", tags=["settings"])

_SINGLETON_ID = 1


async def _get_or_create(db: AsyncSession) -> AppSettings:
    s = await db.get(AppSettings, _SINGLETON_ID)
    if not s:
        s = AppSettings(id=_SINGLETON_ID)
        db.add(s)
        try:
            await db.commit()
        except Exception:
            # Another request may have created the singleton concurrently.
            await db.rollback()
            s = await db.get(AppSettings, _SINGLETON_ID)
            if not s:
                raise
        await db.refresh(s)
    return s


# ── Schemas ───────────────────────────────────────────────

class SettingsOut(BaseModel):
    llm_provider: str
    llm_model: str
    ollama_base_url: str
    nvidia_base_url: str
    custom_base_url: str | None
    cron_hour: int
    cron_minute: int
    cron_enabled: bool
    cron_frequency: str
    cron_day_of_week: int
    agent_budget_per_run: int
    llm_budget_hard_stop: int
    available_providers: dict[str, str]
    # Social trend scan (Apify)
    social_keywords: list[str]
    social_platforms: list[str]
    social_window_days: int
    social_max_per_query: int
    social_scan_enabled: bool
    social_scan_frequency: str
    social_scan_hour: int
    social_include_kols: bool
    facebook_page_urls: list[str]
    apify_configured: bool
    social_lang_filter: str


class SettingsUpdate(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None
    ollama_base_url: str | None = None
    nvidia_base_url: str | None = None
    custom_base_url: str | None = None
    cron_hour: int | None = None
    cron_minute: int | None = None
    cron_enabled: bool | None = None
    cron_frequency: str | None = None
    cron_day_of_week: int | None = None
    agent_budget_per_run: int | None = None
    llm_budget_hard_stop: int | None = None
    social_keywords: list[str] | None = None
    social_platforms: list[str] | None = None
    social_window_days: int | None = None
    social_max_per_query: int | None = None
    social_scan_enabled: bool | None = None
    social_scan_frequency: str | None = None
    social_scan_hour: int | None = None
    social_include_kols: bool | None = None
    facebook_page_urls: list[str] | None = None
    social_lang_filter: str | None = None  # "fr" | "en" | "all"


class TestConnectionRequest(BaseModel):
    provider: str | None = None    # if None, use current DB provider
    model: str | None = None


def _to_out(s: AppSettings) -> SettingsOut:
    import json
    from app.config import get_settings as _gs

    def _jlist(raw: str | None, default: list) -> list:
        if not raw:
            return default
        try:
            v = json.loads(raw)
            return v if isinstance(v, list) else default
        except Exception:
            return default

    return SettingsOut(
        llm_provider=s.llm_provider,
        llm_model=s.llm_model,
        ollama_base_url=s.ollama_base_url or "http://localhost:11434",
        nvidia_base_url=s.nvidia_base_url or "https://integrate.api.nvidia.com/v1",
        custom_base_url=s.custom_base_url,
        cron_hour=s.cron_hour,
        cron_minute=s.cron_minute,
        cron_enabled=s.cron_enabled,
        cron_frequency=s.cron_frequency or "weekly",
        cron_day_of_week=s.cron_day_of_week if s.cron_day_of_week is not None else 1,
        agent_budget_per_run=s.agent_budget_per_run,
        llm_budget_hard_stop=s.llm_budget_hard_stop,
        available_providers=PROVIDERS,
        social_keywords=_jlist(s.social_keywords, []),
        social_platforms=_jlist(s.social_platforms, ["instagram", "twitter", "linkedin", "facebook"]),
        social_window_days=s.social_window_days if s.social_window_days is not None else 180,
        social_max_per_query=s.social_max_per_query if s.social_max_per_query is not None else 30,
        social_scan_enabled=bool(s.social_scan_enabled),
        social_scan_frequency=s.social_scan_frequency or "weekly",
        social_scan_hour=s.social_scan_hour if s.social_scan_hour is not None else 6,
        social_include_kols=bool(s.social_include_kols),
        facebook_page_urls=_jlist(s.facebook_page_urls, []),
        apify_configured=bool(_gs().apify_api_token),
        social_lang_filter=getattr(s, "social_lang_filter", "fr") or "fr",
    )


# ── Endpoints ─────────────────────────────────────────────

@router.get("/", response_model=SettingsOut)
async def get_settings(db: AsyncSession = Depends(get_db)):
    s = await _get_or_create(db)
    return _to_out(s)


@router.post("/", response_model=SettingsOut, dependencies=[Depends(require_admin)])
async def update_settings(body: SettingsUpdate, db: AsyncSession = Depends(get_db)):
    s = await _get_or_create(db)
    if body.llm_provider is not None and body.llm_provider not in PROVIDERS:
        raise HTTPException(status_code=422, detail=f"Unknown provider: {body.llm_provider}")
    import json
    data = body.model_dump(exclude_none=True)
    # social_keywords / social_platforms are stored as JSON strings
    for jfield in ("social_keywords", "social_platforms", "facebook_page_urls"):
        if jfield in data:
            data[jfield] = json.dumps(data[jfield])
    for field, value in data.items():
        setattr(s, field, value)

    await db.commit()
    await db.refresh(s)
    return _to_out(s)


@router.get("/providers")
async def list_providers():
    return {"providers": PROVIDERS}


@router.post("/models", dependencies=[Depends(require_admin)])
async def fetch_models(body: TestConnectionRequest, db: AsyncSession = Depends(get_db)):
    """Return available model IDs for the given (or current) provider. Keys come from env vars."""
    from app.services.llm_router import list_models

    s = await _get_or_create(db)
    provider = body.provider or s.llm_provider
    loop = asyncio.get_event_loop()
    models = await loop.run_in_executor(None, partial(list_models, provider, s))
    return {"provider": provider, "models": models}


@router.post("/test-connection", dependencies=[Depends(require_admin)])
async def test_connection(body: TestConnectionRequest, db: AsyncSession = Depends(get_db)):
    """Ping the LLM provider to verify credentials. Keys come from env vars."""
    from app.services.llm_router import test_connection as _test

    s = await _get_or_create(db)
    provider = body.provider or s.llm_provider
    model = body.model or s.llm_model
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, partial(_test, provider, model, s))
    if not result["ok"]:
        raise HTTPException(status_code=502, detail=result.get("error", "Connection failed"))
    return {"ok": True, "provider": provider, "model": model}
