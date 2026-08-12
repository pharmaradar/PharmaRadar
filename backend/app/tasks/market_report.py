"""Celery task for ad-hoc market-research reports.

The generator lives in services/market_report; this owns the row lifecycle
(pending → running → done|failed) that the UI polls.
"""
import json

import structlog

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

# Sections stored as JSON text; everything else is prose or a scalar.
_JSON_FIELDS = ("subtopics", "voice_rows", "volume", "key_posts", "sources")

_TEXT_FIELDS = ("exec_summary", "so_what", "what_is_said", "voices_note", "volume_note")


def _apply(row, result: dict) -> None:
    for name in _TEXT_FIELDS:
        setattr(row, name, result.get(name) or None)
    for name in _JSON_FIELDS:
        setattr(row, name, json.dumps(result.get(name) or []))
    row.item_count = int(result.get("item_count") or 0)
    row.voice_exact_share = int(result.get("voice_exact_share") or 0)
    row.pdf_url = result.get("pdf_url")


@celery_app.task(
    bind=True,
    name="app.tasks.market_report.generate_market_report",
    queue="llm",
    # One LLM call plus a PDF render, triggered by a user who can simply ask
    # again. acks_late would re-run and re-spend it after a worker restart.
    acks_late=False,
    reject_on_worker_lost=False,
    max_retries=0,
    soft_time_limit=600,
    time_limit=720,
)
def generate_market_report(self, report_id: int) -> dict:
    import asyncio

    from app.services import market_report as mr

    async def _load():
        from app.database import CelerySessionLocal
        from app.models import MarketReport
        async with CelerySessionLocal() as session:
            row = await session.get(MarketReport, report_id)
            if not row:
                return None
            row.status = "running"
            await session.commit()
            return {
                "question": row.question,
                "window_days": row.window_days,
                "language": row.language,
            }

    async def _save(result: dict | None, error: str | None):
        from app.database import CelerySessionLocal
        from app.models import MarketReport
        async with CelerySessionLocal() as session:
            row = await session.get(MarketReport, report_id)
            if not row:
                return
            if error:
                row.status, row.error = "failed", error
            else:
                _apply(row, result)
                # A "no material" result is a real outcome, not a failure: the
                # report explains why it is empty instead of showing an error.
                row.status = "done"
                row.error = result.get("error")
            await session.commit()

    scope = asyncio.run(_load())
    if scope is None:
        logger.warning("market_report.missing_row", report_id=report_id)
        return {"error": "report_not_found"}

    logger.info("market_report.started", report_id=report_id, question=scope["question"][:80])
    try:
        result = mr.build(
            scope["question"],
            window_days=scope["window_days"],
            language=scope["language"],
        )
    except Exception as exc:
        message = str(exc)[:300]
        logger.error("market_report.failed", report_id=report_id, error=message)
        asyncio.run(_save(None, message))
        return {"error": message}

    asyncio.run(_save(result, None))
    logger.info(
        "market_report.done",
        report_id=report_id,
        items=result.get("item_count"),
        pdf=bool(result.get("pdf_url")),
    )
    return {"report_id": report_id, "item_count": result.get("item_count")}
