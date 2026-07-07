import logging
from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

# Silence noisy libraries so celery logs stay readable
for _lib in ("fonttools", "weasyprint", "PIL", "httpx", "httpcore", "urllib3"):
    logging.getLogger(_lib).setLevel(logging.WARNING)

# ── Sentry (worker-side) ──────────────────────────────────
# main.py inits Sentry for the FastAPI process. The worker is a SEPARATE
# process, so without this block every task crash (scrape timeout, LLM 403,
# OOM, PDF render fail) goes unreported. Gated on the same DSN — no-op locally
# when SENTRY_DSN is unset.
if settings.sentry_dsn:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=0.1,
        integrations=[CeleryIntegration()],
    )

celery_app = Celery(
    "pharmaradar",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.scrape",   # scrape_target (wave1) + wave2_rescue
        "app.tasks.llm",
        "app.tasks.pdf",
        "app.tasks.scheduler",
        "app.tasks.maintenance",  # reap_stale_runs
        "app.tasks.social",       # social_scan (Apify)
    ],
)

# Force-import each task module so all @celery_app.task decorators register
# before workers come online. Belt-and-suspenders with the `include=` list above —
# the include alone has been observed to silently skip modules.
import app.tasks.scrape          # noqa: E402,F401
import app.tasks.llm             # noqa: E402,F401
import app.tasks.pdf             # noqa: E402,F401
import app.tasks.scheduler       # noqa: E402,F401
import app.tasks.maintenance     # noqa: E402,F401
import app.tasks.social          # noqa: E402,F401

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Paris",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    result_expires=3600,  # drop task results after 1h — no beat/UI consumes them after that; keeps Redis memory flat across the weekly burst
    # ── Hard guards against wedged tasks ────────────────────────────────
    # Soft limit raises SoftTimeLimitExceeded → task can cleanup / log.
    # Hard limit SIGKILLs the worker child process if it ignores soft.
    # Together they prevent the "4 slots wedged on one stuck scrape" bug.
    task_soft_time_limit=300,   # 5 min  (scrape can do many fetches; allow headroom)
    task_time_limit=360,        # 6 min  hard kill
    task_routes={
        "app.tasks.scrape.*": {"queue": "scrape"},
        "app.tasks.llm.*": {"queue": "llm"},
        "app.tasks.pdf.*": {"queue": "pdf"},
        "app.tasks.scheduler.*": {"queue": "llm"},
        "app.tasks.maintenance.*": {"queue": "llm"},
        "app.tasks.social.*": {"queue": "scrape"},
    },
    # ── Per-task overrides where the default is wrong ───────────────────
    # Agent rescue can hit 180s timeouts repeatedly; give it more room.
    task_annotations={
        "app.tasks.scrape.wave2_rescue": {
            "soft_time_limit": 600,   # 10 min
            "time_limit":      720,   # 12 min
        },
        "app.tasks.scrape.scrape_target": {
            "soft_time_limit": 480,   # 8 min  — many parallel fetches
            "time_limit":      600,   # 10 min
        },
    },
    # Beat schedule
    beat_schedule={
        "check-daily-run": {
            "task": "app.tasks.scheduler.check_daily_run",
            "schedule": crontab(minute="*"),
        },
        "check-social-scan": {
            "task": "app.tasks.scheduler.check_social_scan",
            "schedule": crontab(minute="*"),
        },
        # Reaper: every 5 min, mark any 'running' RunLog older than 1h as 'error'
        # and revoke its child task IDs. Catches anything the time limits miss.
        "reap-stale-runs": {
            "task": "app.tasks.maintenance.reap_stale_runs",
            "schedule": 300.0,
        },
    },
)
