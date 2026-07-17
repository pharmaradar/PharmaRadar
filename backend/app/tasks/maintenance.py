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

reap_stale_reports
───────────────────
Same idea for burning_topic_reports (Burning Topics + Congress reports share
this table). Unlike RunLog, a report row has no celery_task_id to revoke and
generate_topic_report already checks `_aborted()` between phases — so this
only needs to flip the DB status. Its real job is unsticking the case the
task's own abort-check can't catch: the worker process itself dying (crash,
OOM-kill, manual restart) mid-task, which leaves the report in `pending` or
`running` forever with no automatic recovery. Since generate-report refuses a
new run while one is in-flight, a report only this reaper can rescue would
otherwise block that topic/congress from ever generating again.
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

# generate_topic_report's own hard time limit is 720s (12 min) — a report
# still pending/running well past that was orphaned by a dead worker, not a
# task that's merely slow.
STALE_REPORT_AFTER_SECONDS = 20 * 60   # 20 minutes


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


@celery_app.task(name="app.tasks.maintenance.reap_stale_reports", queue="llm")
def reap_stale_reports() -> dict:
    """Beat-fired every 5 min. Returns a small dict for log visibility."""
    return asyncio.run(_reap_reports())


async def _reap_reports() -> dict:
    from sqlalchemy import select

    from app.database import CelerySessionLocal
    from app.models import BurningTopicReport

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_REPORT_AFTER_SECONDS)
    reaped: list[int] = []

    async with CelerySessionLocal() as sess:
        result = await sess.execute(
            select(BurningTopicReport).where(
                BurningTopicReport.status.in_(["pending", "running"]),
                BurningTopicReport.created_at < cutoff,
            )
        )
        stale = result.scalars().all()

        for report in stale:
            original_status = report.status
            report.status = "failed"
            report.summary_md = (
                f"Report generation was interrupted (stuck in '{original_status}' for > "
                f"{STALE_REPORT_AFTER_SECONDS}s — likely a worker restart) and has been "
                "reset. Generate the report again."
            )
            reaped.append(report.id)

        if stale:
            await sess.commit()

    if reaped:
        logger.warning("reap.stale_reports_killed", report_ids=reaped, cutoff=cutoff.isoformat())
    return {"reaped": reaped}


# ── Adverse-event backfill ────────────────────────────────
# Newly scraped KOL posts get classified inline by the extractor (same LLM
# call). This sweep covers everything else: social posts (which never get a
# per-post LLM call at ingest) and any scraped post whose extraction predates
# the AE feature or whose classification block failed to parse.

_AE_BATCH_TOTAL = 30       # max posts classified per sweep (beat fires every 4h)
_AE_BATCH_PER_CALL = 15    # posts per LLM call — compact prompt, cheap model

_AE_CLASSIFY_PROMPT = (
    "You are a pharmacovigilance classifier for a pharma intelligence system.\n"
    "For EACH numbered post below, decide: does it REPORT A SPECIFIC PATIENT "
    "experiencing a negative reaction, side effect, or harm from a drug (an "
    "individual case report)? General discussion of side-effect profiles in "
    "studies, trials, labels, or reviews is NOT an adverse event report.\n\n"
    "Return ONLY a JSON array, one object per post, no markdown:\n"
    '[{"id": <post number>, "ae": true|false, "reason": "<if true: drug + reaction, <=15 words; else null>"}]\n\n'
    "POSTS:\n{posts_block}"
)


@celery_app.task(name="app.tasks.maintenance.classify_ae_backfill", queue="llm",
                 soft_time_limit=600, time_limit=720)
def classify_ae_backfill() -> dict:
    """Beat-fired. Classifies a small batch of unclassified posts per sweep —
    rate-limit pacing comes from the batch cap + llm_router's built-in
    exponential backoff on 429s."""
    return asyncio.run(_classify_ae())


def _classify_llm_batch(items: list[tuple[int, str]]) -> dict[int, tuple[bool, str | None]]:
    """items: [(local_id, text)] → {local_id: (is_ae, reason)}. Empty on failure."""
    import json
    import re

    from app.services.llm_router import call_llm

    block = "\n".join(f"[{i}] {text[:600]}" for i, text in items)
    try:
        raw = call_llm(
            [{"role": "user", "content": _AE_CLASSIFY_PROMPT.replace("{posts_block}", block)}],
            temperature=0.0, max_tokens=1500,
        )
        cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()
        m = re.search(r"\[.*\]", cleaned, re.DOTALL)
        parsed = json.loads(m.group(0)) if m else []
        out: dict[int, tuple[bool, str | None]] = {}
        for row in parsed:
            if isinstance(row, dict) and isinstance(row.get("id"), int) and isinstance(row.get("ae"), bool):
                out[row["id"]] = (row["ae"], (row.get("reason") or None) if row["ae"] else None)
        return out
    except Exception as exc:
        logger.warning("ae_backfill.llm_failed", exc=str(exc)[:200])
        return {}


async def _classify_ae() -> dict:
    from sqlalchemy import select

    from app.database import CelerySessionLocal
    from app.models import ScrapedPost, SocialPost

    classified = 0
    flagged = 0

    async with CelerySessionLocal() as sess:
        half = _AE_BATCH_TOTAL // 2
        scraped = (await sess.execute(
            select(ScrapedPost)
            .where(ScrapedPost.is_adverse_event.is_(None), ScrapedPost.raw_content.is_not(None))
            .order_by(ScrapedPost.scraped_at.desc()).limit(half)
        )).scalars().all()
        social = (await sess.execute(
            select(SocialPost)
            .where(SocialPost.is_adverse_event.is_(None), SocialPost.text.is_not(None))
            .order_by(SocialPost.scraped_at.desc()).limit(_AE_BATCH_TOTAL - len(scraped))
        )).scalars().all()

        pending = [(p, (p.raw_content or "")) for p in scraped] + [(p, (p.text or "")) for p in social]
        if not pending:
            return {"classified": 0, "flagged": 0}

        def _classify_chunk(chunk: list) -> None:
            """Classify a chunk; on total failure bisect down to singles so one
            poison post (LLM refusal / malformed JSON) can't block the rest —
            without this, a bad batch got re-selected every sweep forever."""
            nonlocal classified, flagged
            if not chunk:
                return
            results = _classify_llm_batch([(i, text) for i, (_, text) in enumerate(chunk, 1)])
            if not results and len(chunk) > 1:
                mid = len(chunk) // 2
                _classify_chunk(chunk[:mid])
                _classify_chunk(chunk[mid:])
                return
            for i, (post, _) in enumerate(chunk, 1):
                if i in results:
                    is_ae, reason = results[i]
                    post.is_adverse_event = is_ae
                    post.ae_reason = reason
                    classified += 1
                    if is_ae:
                        flagged += 1

        for chunk_start in range(0, len(pending), _AE_BATCH_PER_CALL):
            _classify_chunk(pending[chunk_start:chunk_start + _AE_BATCH_PER_CALL])
        await sess.commit()

    if classified:
        logger.info("ae_backfill.done", classified=classified, flagged=flagged)
    return {"classified": classified, "flagged": flagged}
