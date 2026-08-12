"""Scheduler — Celery beat fires check_scheduled_run every minute.

Run cadence is weekly or monthly, configured via AppSettings. Daily was removed
on the client's request: a report covering a 30-day window has nothing new to
say every 24 hours, and each run spends real scraping credit.

Rows still holding the retired "daily" value are treated as weekly rather than
firing every day — migration 025 rewrites them, but a worker may briefly see an
un-migrated row, and defaulting to the cheaper cadence is the safe direction.
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import structlog

from app.tasks.celery_app import celery_app
from app.config import get_settings

logger = structlog.get_logger(__name__)

_CRON_TZ = ZoneInfo("Europe/Paris")

_DOW_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

VALID_FREQUENCIES = ("weekly", "monthly")


def _normalise_frequency(value: str | None) -> str:
    """Coerce a stored cadence to a supported one, defaulting to weekly."""
    freq = (value or "").strip().lower()
    return freq if freq in VALID_FREQUENCIES else "weekly"


@celery_app.task(name="app.tasks.scheduler.check_scheduled_run", queue="llm")
def check_scheduled_run() -> None:
    """Fires every minute and triggers the pipeline when the configured time matches.

    Named for what it does, not how often beat pokes it: the cadence itself is
    weekly or monthly and lives in AppSettings.cron_frequency.
    """
    asyncio.run(_check())


# Compatibility alias. A message published by the previous beat can still be in
# the queue during a deploy; without this the worker rejects it as unregistered.
# Safe to delete once one scheduled run has completed after rollout.
@celery_app.task(name="app.tasks.scheduler.check_daily_run", queue="llm")
def check_daily_run() -> None:
    asyncio.run(_check())


async def _check() -> None:
    from app.database import CelerySessionLocal
    from app.models import AppSettings, RunLog

    async with CelerySessionLocal() as sess:
        s = await sess.get(AppSettings, 1)
        if not s or not s.cron_enabled:
            return

        now = datetime.now(_CRON_TZ)

        # Hour + minute must match
        if now.hour != s.cron_hour or now.minute != s.cron_minute:
            return

        frequency = _normalise_frequency(getattr(s, "cron_frequency", "weekly"))
        if frequency == "monthly":
            # Day of month is capped at 28 so every month has one.
            target_dom = min(max(int(getattr(s, "cron_day_of_month", 1) or 1), 1), 28)
            if now.day != target_dom:
                return
        else:
            # Weekly: 0=Mon … 6=Sun, matching Python's weekday().
            target_dow = getattr(s, "cron_day_of_week", 1) or 1
            if now.weekday() != target_dow:
                return

        # Don't double-trigger within 2 minutes
        from sqlalchemy import select, func
        cutoff = now - timedelta(minutes=2)
        recent = await sess.execute(
            select(func.count()).select_from(RunLog)
            .where(RunLog.started_at >= cutoff)
        )
        if recent.scalar() > 0:
            logger.info("scheduler.skip_already_ran_recently")
            return

        # Capture to locals before session closes — avoids DetachedInstanceError
        cron_hour = s.cron_hour
        cron_minute = s.cron_minute
        cron_dow = getattr(s, "cron_day_of_week", 1) or 1

    dow_name = _DOW_NAMES[cron_dow]
    logger.info("scheduler.triggering",
                frequency=frequency,
                day=dow_name if frequency == "weekly" else f"day {getattr(s, 'cron_day_of_month', 1)}",
                hour=cron_hour, minute=cron_minute)

    import httpx
    try:
        from app.auth import internal_token
        settings = get_settings()
        r = httpx.post(settings.run_trigger_url, json={},
                       headers={"X-Internal-Token": internal_token()}, timeout=10)
        if r.status_code >= 400:
            logger.warning("scheduler.trigger_failed",
                           status=r.status_code, body=(r.text or "")[:200])
    except Exception as exc:
        logger.warning("scheduler.trigger_failed", exc=str(exc))


@celery_app.task(name="app.tasks.scheduler.check_social_scan", queue="llm")
def check_social_scan() -> None:
    """Fires every minute. Triggers the social trend scan when enabled and the
    configured time matches. Daily → every day at the hour; weekly → Mondays."""
    asyncio.run(_check_social())


async def _check_social() -> None:
    import json
    from app.database import CelerySessionLocal
    from app.models import AppSettings

    async with CelerySessionLocal() as sess:
        s = await sess.get(AppSettings, 1)
        if not s or not getattr(s, "social_scan_enabled", False):
            return

        now = datetime.now(_CRON_TZ)
        # Fire at the top of the configured hour
        if now.hour != getattr(s, "social_scan_hour", 6) or now.minute != 0:
            return
        # Weekly mode runs on Mondays (no dedicated day-of-week field for social)
        frequency = getattr(s, "social_scan_frequency", "weekly") or "weekly"
        if frequency == "weekly" and now.weekday() != 0:
            return

    # Skip if a scan is already running
    try:
        import redis as _redis
        from app.tasks.social import _STATUS_KEY
        r = _redis.Redis.from_url(get_settings().redis_url, socket_timeout=2)
        cur = r.get(_STATUS_KEY)
        if cur and json.loads(cur).get("running"):
            logger.info("scheduler.social_skip_already_running")
            return
    except Exception:
        pass

    logger.info("scheduler.social_triggering", frequency=frequency)
    from app.tasks.social import social_scan
    social_scan.delay()
