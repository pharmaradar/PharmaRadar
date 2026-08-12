from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # DB — Railway provides DATABASE_URL as postgresql://... we convert to asyncpg
    database_url: str = Field(
        default="postgresql+asyncpg://pharmaradar:pharmaradar@localhost:5432/pharmaradar",
        validation_alias="DATABASE_URL"
    )

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # Vertex AI
    google_cloud_project: str = ""
    google_cloud_location: str = "europe-west4"
    google_application_credentials: str = ""

    # Provider API keys
    openrouter_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    nvidia_api_key: str = ""
    gemini_api_key: str = ""

    # TinyFish
    tinyfish_api_key: str = ""
    tinyfish_api_keys: str = ""
    # 60/min is the plan's documented search limit and the most restrictive of
    # the three (search 60/min, fetch 300/min, agent 10 concurrent) — one shared
    # counter has to respect the tightest. 30 was the free-tier throttle and
    # halved throughput for no reason: at 50 targets the French scope issues
    # ~1800 searches, which is ~60 min of pure waiting at 30/min and would run
    # into the stale-run reaper.
    tinyfish_rate_limit_per_key: int = 60
    # Credits granted per key per month (no balance API). 1650 = the Starter plan
    # allowance. Only agent runs consume these — search and fetch are unmetered,
    # see _billable_steps in services/scraper.py.
    tinyfish_monthly_credits: int = 1650

    # Sentry
    sentry_dsn: str = ""

    # Embeddings
    voyage_api_key: str = ""

    # Apify — social media scraping (Instagram, X, LinkedIn, Facebook)
    apify_api_token: str = ""

    # Vercel Blob storage
    vercel_blob_token: str = ""

    # Auth — first admin is seeded from these on startup if no users exist
    seed_admin_email: str = ""
    seed_admin_password: str = ""
    seed_admin_name: str = ""

    # App
    secret_key: str = "changeme-at-least-32-chars-long!!"
    environment: str = "development"
    log_level: str = "INFO"
    reports_dir: str = "./reports"

    # CORS — comma-separated origins. "*" (default) allows any origin; pin this
    # to the Vercel frontend URL (+ localhost for dev) once it's known.
    allowed_origins: str = "*"

    # Internal trigger URL — on Railway set to https://your-backend.railway.app/api/runs/trigger
    run_trigger_url: str = "http://localhost:8009/api/runs/trigger"

    # Pipeline tunables
    agent_budget_per_run: int = 250
    llm_budget_hard_stop: int = 500

    @property
    def async_database_url(self) -> str:
        """Convert Railway's postgresql:// URL to asyncpg format."""
        url = self.database_url
        if url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgres://") and "+asyncpg" not in url:
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        return url

    @property
    def tinyfish_keys_list(self) -> list[str]:
        if self.tinyfish_api_keys:
            return [k.strip() for k in self.tinyfish_api_keys.split(",") if k.strip()]
        if self.tinyfish_api_key:
            return [self.tinyfish_api_key]
        return []

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def allowed_origins_list(self) -> list[str]:
        if self.allowed_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

