"""Celery task for the three dashboard synthesis PDFs.

The build itself lives in services/synthesis_report.py; this is only the queue
wrapper plus the status bookkeeping the dashboard polls.
"""
import structlog

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.synthesis.generate_synthesis_report",
    queue="llm",
    # One LLM call plus a PDF render. acks_late would re-run the whole thing on a
    # worker restart and spend the call twice for a report the user can simply
    # re-trigger, so this follows the same choice as the other on-demand reports.
    acks_late=False,
    reject_on_worker_lost=False,
    max_retries=0,
    soft_time_limit=600,
    time_limit=720,
)
def generate_synthesis_report(self, scope: str) -> dict:
    from app.services import synthesis_report as sr

    try:
        sr.spec_for(scope)
    except ValueError as exc:
        logger.warning("synthesis.bad_scope", scope=scope, error=str(exc))
        sr.set_status(scope, status="error", error=str(exc))
        return {"error": str(exc)}

    sr.set_status(scope, status="running")
    logger.info("synthesis.started", scope=scope)
    try:
        result = sr.build(scope)
    except Exception as exc:
        message = str(exc)[:300]
        logger.error("synthesis.failed", scope=scope, error=message)
        sr.set_status(scope, status="error", error=message)
        return {"error": message}

    sr.store_result(scope, result)
    # A missing-data result is a real, storable outcome — the dashboard shows the
    # explanation rather than an error state.
    sr.set_status(scope, status="done", error=result.get("error"))
    logger.info(
        "synthesis.done",
        scope=scope,
        insights=result.get("insight_count"),
        pdf=bool(result.get("pdf_url")),
    )
    return {
        "scope": scope,
        "insight_count": result.get("insight_count"),
        "pdf_url": result.get("pdf_url"),
    }


# ── Refresh every dashboard synthesis ─────────────────────

@celery_app.task(
    bind=True,
    name="app.tasks.synthesis.refresh_all_syntheses",
    queue="llm",
    # Not acks_late: a redelivered refresh would pay for the same analysis
    # twice, and losing one costs only freshness until the next sweep.
    acks_late=False,
    soft_time_limit=2400,
    time_limit=2700,
)
def refresh_all_syntheses(self, reason: str = "manual") -> dict:
    """Regenerate the KOL, competitor, comprehensive and global syntheses.

    These were generate-on-demand, so after a pipeline run the client had to
    press four separate buttons; whatever they missed silently showed an older
    analysis of a newer corpus.

    Scopes run in sequence rather than in parallel: they hit the same LLM
    provider, and firing four at once is the reliable way to earn a 429 that
    leaves half the dashboard stale.
    """
    import asyncio

    from app.services import synthesis_report as sr

    done, failed = [], []
    for scope in sr.SCOPES:
        try:
            generate_synthesis_report.run(scope)
            done.append(scope)
        except Exception as exc:                    # noqa: BLE001 - one scope must not stop the rest
            logger.warning("synthesis.refresh_scope_failed", scope=scope, error=str(exc)[:180])
            failed.append(scope)

    # The global synthesis merges the briefs, so it runs last — before this it
    # would have summarised the previous generation's output.
    try:
        from app.tasks.llm import generate_global_synthesis
        generate_global_synthesis.run()
        done.append("global")
    except Exception as exc:                        # noqa: BLE001
        logger.warning("synthesis.refresh_global_failed", error=str(exc)[:180])
        failed.append("global")

    async def _stamp() -> None:
        from datetime import datetime, timezone

        from app.database import CelerySessionLocal
        from app.models import AppSettings
        async with CelerySessionLocal() as sess:
            settings = await sess.get(AppSettings, 1)
            if settings:
                settings.auto_synthesis_last_run = datetime.now(timezone.utc)
                await sess.commit()

    try:
        asyncio.run(_stamp())
    except Exception as exc:                        # noqa: BLE001
        logger.debug("synthesis.stamp_failed", error=str(exc)[:120])

    logger.info("synthesis.refresh_all", reason=reason, done=done, failed=failed)
    return {"reason": reason, "generated": done, "failed": failed}
