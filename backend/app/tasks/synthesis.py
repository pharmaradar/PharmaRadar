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
