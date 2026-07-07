"""Scheduler — Celery beat fires check_scheduled_run every minute.

Supports both daily and weekly run modes, configured via AppSettings.
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


@celery_app.task(name="app.tasks.scheduler.check_daily_run", queue="llm")
def check_daily_run() -> None:
    """Fires every minute. Triggers pipeline when hour/minute (and optionally day) match."""
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

        # Weekly mode: also check day of week (0=Mon … 6=Sun, matches Python's weekday())
        frequency = getattr(s, "cron_frequency", "weekly") or "weekly"
        if frequency == "weekly":
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
                day=dow_name if frequency == "weekly" else "every day",
                hour=cron_hour, minute=cron_minute)

    import httpx
    try:
        settings = get_settings()
        r = httpx.post(settings.run_trigger_url, json={}, timeout=10)
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
