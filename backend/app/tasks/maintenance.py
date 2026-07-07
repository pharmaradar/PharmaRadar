"""Maintenance tasks — runs on a beat schedule.

reap_stale_runs
───────────────
Scans for RunLog rows still in `running` state whose `started_at` is older
than STALE_RUN_AFTER_SECONDS. Marks them `error` and revokes their stored
celery_task_id (and any children Celery knows about).

This is the safety net for the "all 4 worker slots wedged on a stuck scrape"
class of bug. The Celery `task_time_limit` config should kill individual
tasks before they hit this — but if a task hangs in C code, ignores SIGTERM,
or the orchestrating chord errback never fires, the reaper still resolves
the run so the next scheduled trigger isn't blocked.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

# A run that's been "running" for over an hour is almost certainly dead.
# Even a 100-KOL scrape with rescue should comfortably finish in < 30 min.
STALE_RUN_AFTER_SECONDS = 60 * 60   # 1 hour


@celery_app.task(name="app.tasks.maintenance.reap_stale_runs", queue="llm")
def reap_stale_runs() -> dict:
    """Beat-fired every 5 min. Returns a small dict for log visibility."""
    return asyncio.run(_reap())


async def _reap() -> dict:
    from sqlalchemy import select

    from app.database import CelerySessionLocal
    from app.models import RunLog, RunStatus

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_RUN_AFTER_SECONDS)
    reaped: list[int] = []
    revoked_ids: list[str] = []

    async with CelerySessionLocal() as sess:
        result = await sess.execute(
            select(RunLog).where(
                RunLog.status == RunStatus.running,
                RunLog.started_at < cutoff,
            )
        )
        stale = result.scalars().all()

        for run in stale:
            run.status = RunStatus.error
            run.error_message = (
                f"reaped: stuck in 'running' for > {STALE_RUN_AFTER_SECONDS}s; "
                "likely worker hang or lost task"
            )
            run.completed_at = datetime.now(timezone.utc)
            run.current_target = None
            reaped.append(run.id)
            if run.celery_task_id:
                revoked_ids.append(run.celery_task_id)

        if stale:
            await sess.commit()

    # Best-effort: ask Celery to terminate the orchestrating task(s).
    # Their children inherit revoke via the chord/group machinery — and the
    # task_time_limit config will SIGKILL anything still alive.
    if revoked_ids:
        try:
            celery_app.control.revoke(revoked_ids, terminate=True, signal="SIGTERM")
        except Exception as exc:
            logger.warning("reap.revoke_failed", exc=str(exc), ids=revoked_ids)

    if reaped:
        logger.warning("reap.stale_runs_killed", run_ids=reaped,
                       revoked=revoked_ids, cutoff=cutoff.isoformat())
    return {"reaped": reaped, "revoked_task_ids": revoked_ids}
