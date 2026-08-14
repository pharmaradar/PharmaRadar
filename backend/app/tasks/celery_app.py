import logging
from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

# Silence noisy libraries so celery logs stay readable. Note: the package is
# "fontTools" (capital T) — logger names are case-sensitive, so "fonttools"
# here never matched and every glyph-subsetting debug line leaked through.
for _lib in ("fontTools", "weasyprint", "PIL", "httpx", "httpcore", "urllib3"):
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
        "app.tasks.burning_topics",  # generate_topic_report
        "app.tasks.synthesis",       # dashboard KOL/competitor/comprehensive PDFs
        "app.tasks.market_report",   # ad-hoc Topic Explorer market-research reports
        "app.tasks.accounts",        # per-account tracking sweep + on-demand refresh
        "app.tasks.literature",      # Europe PMC publications + ClinicalTrials.gov
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
import app.tasks.burning_topics  # noqa: E402,F401
import app.tasks.synthesis       # noqa: E402,F401
import app.tasks.market_report   # noqa: E402,F401
import app.tasks.accounts       # noqa: E402,F401
import app.tasks.literature     # noqa: E402,F401

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
    # ── Redelivery after a worker dies mid-task ──────────────────────────
    # With acks_late on a Redis broker, a task killed mid-flight stays UNACKED
    # and only redelivers after visibility_timeout. Celery's default is 3600s —
    # exactly the same as reap_stale_runs' threshold. But the reaper counts from
    # RUN START while this counts from WORKER DEATH, and the worker always dies
    # after the run began, so the reaper always won and stranded tasks could
    # NEVER self-recover. (Cost 3 targets on 2026-08-08 when a Railway env-var
    # change redeployed the workers mid-run.) 900s puts redelivery comfortably
    # inside the reaper's window. task_reject_on_worker_lost only covers a dying
    # CHILD under a live parent — it does nothing when the container is killed.
    broker_transport_options={"visibility_timeout": 900},
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
        "app.tasks.burning_topics.*": {"queue": "scrape"},
    },
    # ── Per-task overrides where the default is wrong ───────────────────
    # NOTE: these annotations OVERRIDE the decorators' own soft/time_limit args
    # (Celery gives task_annotations precedence) — keep them in sync with the
    # decorators or the decorator values silently never apply.
    # Agent rescue processes MANY targets sequentially (agent calls are 120s+
    # each), so it needs the full 30 min — the old 600/720 here was silently
    # SIGKILLing large rescue batches at 12 min.
    task_annotations={
        "app.tasks.scrape.wave2_rescue": {
            "soft_time_limit": 1800,  # 30 min — matches the decorator's intent
            "time_limit":      1920,  # 32 min hard kill
        },
        "app.tasks.scrape.scrape_target": {
            "soft_time_limit": 480,   # 8 min  — many parallel fetches
            "time_limit":      600,   # 10 min
        },
        # Burning-topic report = DB query + one TinyFish search + LLM + PDF in
        # a single task; the 5-min default is too tight for that chain.
        "app.tasks.burning_topics.generate_topic_report": {
            "soft_time_limit": 600,   # 10 min
            "time_limit":      720,   # 12 min
        },
    },
    # Beat schedule
    beat_schedule={
        # Polls every minute and fires only when the configured weekly/monthly
        # slot matches — the minute cadence is the poll, not the run cadence.
        "check-scheduled-run": {
            "task": "app.tasks.scheduler.check_scheduled_run",
            "schedule": crontab(minute="*"),
        },
        # Every minute, like the scrape cron: the gate reads Settings so the
        # client can move the hour without a redeploy.
        "check-auto-synthesis": {
            "task": "app.tasks.scheduler.check_auto_synthesis",
            "schedule": crontab(minute="*"),
        },
        "check-social-scan": {
            "task": "app.tasks.scheduler.check_social_scan",
            "schedule": crontab(minute="*"),
        },
        # Publications and trials come from free official APIs, so this can run
        # daily without a cost conversation. 03:30 UTC, before the account sweep.
        "sync-publications": {
            "task": "app.tasks.literature.sync_publications",
            "schedule": crontab(hour=3, minute=30),
        },
        "sync-trials": {
            "task": "app.tasks.literature.sync_trials",
            "schedule": crontab(hour=3, minute=50),
        },
        # Press feeds move daily and cost nothing, so they run twice a day.
        "sync-fr-feeds": {
            "task": "app.tasks.literature.sync_fr_feeds",
            "schedule": crontab(hour="6,18", minute=10),
        },
        # Account tracking runs on its own daily cadence, deliberately not
        # coupled to the keyword social scan: the client refreshes individual
        # accounts on demand, and this is the background sweep that keeps the
        # rest current. 04:15 UTC — off-peak, and clear of the scrape window.
        "account-tracking-sweep": {
            "task": "app.tasks.accounts.account_scan",
            "schedule": crontab(hour=4, minute=15),
        },
        # Reaper: every 5 min, mark any 'running' RunLog older than 1h as 'error'
        # and revoke its child task IDs. Catches anything the time limits miss.
        "reap-stale-runs": {
            "task": "app.tasks.maintenance.reap_stale_runs",
            "schedule": 300.0,
        },
        # Same idea for burning_topic_reports (Burning Topics + Congress): a
        # report stuck in pending/running past its own task time limit was
        # orphaned by a dead worker, and the in-flight check would otherwise
        # block that topic/congress from ever generating again.
        "reap-stale-reports": {
            "task": "app.tasks.maintenance.reap_stale_reports",
            "schedule": 300.0,
        },
        # Pharmacovigilance: classify unclassified posts (esp. social — those
        # never get a per-post LLM call at ingest) in small batches. ≤2 cheap
        # LLM calls per sweep; a fully-classified DB makes this a no-op query.
        "classify-ae-backfill": {
            "task": "app.tasks.maintenance.classify_ae_backfill",
            # Hourly, not 4-hourly: an unscreened post is visible to the client
            # until it is classified, so the backlog is an exposure window.
            "schedule": 3600.0,
        },
    },
)
