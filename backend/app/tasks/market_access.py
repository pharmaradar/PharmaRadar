"""Daily sync of French market-access events (HAS rulings, ANSM shortages).

Free official files, no key, no per-call cost — same footing as the literature
lanes, so this runs daily without a budget conversation.
"""
from __future__ import annotations

import asyncio

import structlog

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.market_access.sync_market_access",
    queue="llm",
    # Not acks_late: a redelivered sweep re-downloads five files to insert rows
    # it already has. Nothing is lost by skipping one night.
    acks_late=False,
    soft_time_limit=600,
    time_limit=720,
)
def sync_market_access(self) -> dict:
    return asyncio.run(_sync())


async def _sync() -> dict:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.database import CelerySessionLocal
    from app.models.market_access import MarketAccessEvent
    from app.services.bdpm import collect_events

    # Network + parse off the event loop: collect_events downloads five files
    # and walks ~26k rows synchronously.
    events = await asyncio.get_event_loop().run_in_executor(None, collect_events)
    if not events:
        # Never silently succeed on an empty result. The source files are full
        # snapshots, so zero events means a fetch or parse broke, not that
        # France stopped issuing rulings.
        logger.warning("market_access.no_events")
        return {"fetched": 0, "added": 0, "skipped": 0}

    added = 0
    async with CelerySessionLocal() as sess:
        for event in events:
            stmt = (pg_insert(MarketAccessEvent.__table__)
                    .values(**event)
                    .on_conflict_do_nothing(index_elements=["content_hash"]))
            result = await sess.execute(stmt)
            added += result.rowcount or 0
        await sess.commit()

    out = {"fetched": len(events), "added": added, "skipped": len(events) - added}
    logger.info("market_access.synced", **out)
    return out
