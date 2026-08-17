"""Daily sync of Transparence Santé payments for every tracked target.

Fully automatic: nothing here waits for a human. Targets resolve themselves to
an RPPS on first sight, and the register is polled incrementally afterwards.

Automatic is not the same as optimistic, though. A target only becomes
"resolved" when one national identifier dominates its name-matched declarations;
anything less certain is recorded as "ambiguous" WITH the reason and displays
nothing. That is what keeps "no manual confirmations" and "never show a payment
that belongs to someone else" from being in conflict — the uncertain cases fail
closed instead of guessing, and they cost no one any clicks.

The register publishes daily (hundreds to thousands of new declarations), and
`date_publication` is the cursor — not the payment date, since a 2017 payment
can be filed in 2026.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import structlog

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

# Re-check an already-resolved target's identity occasionally: a declarer can
# start filing under an id that did not exist when we first looked.
_RERESOLVE_AFTER_DAYS = 30

# Overlap the incremental window so a declaration published while a sync was
# running is not skipped. Re-fetching is free — declaration_id is unique.
_SYNC_OVERLAP_DAYS = 3

# Ambiguous/not_found targets are retried, more slowly: the register grows, so
# a name with too few records today may be pinnable next month.
_RETRY_UNRESOLVED_AFTER_DAYS = 14


@celery_app.task(
    bind=True,
    name="app.tasks.transparence.sync_transparence",
    queue="llm",
    # Not acks_late: a redelivered sweep would re-query the register for data it
    # already has. Nothing here is lost by skipping a day.
    acks_late=False,
    soft_time_limit=1500,
    time_limit=1740,
)
def sync_transparence(self, target_ids: list[int] | None = None,
                      force: bool = False) -> dict:
    """Resolve and sync payments. `force` re-resolves and refetches in full."""
    return asyncio.run(_sync(target_ids, force))


async def _sync(target_ids: list[int] | None, force: bool) -> dict:
    from sqlalchemy import select
    from app.database import CelerySessionLocal
    from app.models import Target

    totals = {"checked": 0, "resolved": 0, "ambiguous": 0, "not_found": 0,
              "payments_added": 0, "errors": 0}

    async with CelerySessionLocal() as sess:
        q = select(Target).where(Target.active.is_(True))
        if target_ids:
            q = q.where(Target.id.in_(target_ids))
        targets = (await sess.execute(q)).scalars().all()

    for target in targets:
        try:
            result = await _sync_one(target.id, force)
            totals["checked"] += 1
            status = result.get("status")
            if status in totals:
                totals[status] += 1
            totals["payments_added"] += result.get("added", 0)
        except Exception as exc:                       # noqa: BLE001
            totals["errors"] += 1
            logger.warning("transparence.target_failed", target_id=target.id,
                           error=str(exc)[:200])

    logger.info("transparence.sync_done", **totals)
    return totals


def _needs_resolution(target, force: bool) -> bool:
    if force or not target.transparence_rpps:
        return True
    stamp = target.transparence_resolved_at
    if not stamp:
        return True
    age = (datetime.now(timezone.utc) - stamp).days
    if target.transparence_status == "resolved":
        return age >= _RERESOLVE_AFTER_DAYS
    return age >= _RETRY_UNRESOLVED_AFTER_DAYS


async def _sync_one(target_id: int, force: bool) -> dict:
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.database import CelerySessionLocal
    from app.models import Target
    from app.models.transparence import TransparencePayment
    from app.services.transparence import fetch_payments, normalise_payment, resolve_rpps

    now = datetime.now(timezone.utc)

    async with CelerySessionLocal() as sess:
        target = await sess.get(Target, target_id)
        if not target:
            return {"status": "not_found", "added": 0}

        # Competitors are companies, not individuals — the register lists them
        # as payers, never as beneficiaries. Skipping avoids a pointless lookup
        # and stops "MSD France" resolving to some physician named France.
        #
        # The skip is RECORDED, not just returned: leaving these permanently
        # "unresolved" in the database makes the Targets page look like the sync
        # is broken for them, and someone would eventually go looking for a bug
        # that is really a deliberate design decision.
        if target.target_type == "competitor":
            if target.transparence_status != "not_found":
                target.transparence_status = "not_found"
                target.transparence_note = (
                    "Competitors are payers in the register, not beneficiaries — "
                    "their spend appears on the KOLs they fund.")
                target.transparence_resolved_at = now
                await sess.commit()
            return {"status": "not_found", "added": 0}

        if _needs_resolution(target, force):
            outcome = await asyncio.get_event_loop().run_in_executor(
                None, resolve_rpps, target.name)
            target.transparence_status = outcome["status"]
            target.transparence_confidence = outcome["confidence"]
            target.transparence_note = (outcome.get("note") or "")[:255]
            target.transparence_resolved_at = now
            # Only a confident outcome is allowed to set the pin. An ambiguous
            # re-resolution must CLEAR a previous pin, not silently keep serving
            # payments under an identity we no longer stand behind.
            target.transparence_rpps = outcome["rpps"] if outcome["status"] == "resolved" else None
            await sess.commit()
            logger.info("transparence.resolved", target=target.name,
                        status=outcome["status"], rpps=outcome["rpps"],
                        confidence=outcome["confidence"])

        if target.transparence_status != "resolved" or not target.transparence_rpps:
            return {"status": target.transparence_status, "added": 0}

        rpps = target.transparence_rpps
        since: date | None = None
        if target.transparence_synced_at and not force:
            since = (target.transparence_synced_at - timedelta(days=_SYNC_OVERLAP_DAYS)).date()

    rows = await asyncio.get_event_loop().run_in_executor(
        None, lambda: fetch_payments(rpps, since))

    added = 0
    async with CelerySessionLocal() as sess:
        for row in rows:
            payload = normalise_payment(row)
            if not payload:
                continue
            payload["target_id"] = target_id
            # ON CONFLICT DO NOTHING on the unique declaration_id: the overlap
            # window deliberately re-fetches, and a re-published correction must
            # not create a duplicate payment.
            stmt = (pg_insert(TransparencePayment.__table__)
                    .values(**payload)
                    .on_conflict_do_nothing(index_elements=["declaration_id"]))
            result = await sess.execute(stmt)
            added += result.rowcount or 0

        target = await sess.get(Target, target_id)
        if target:
            target.transparence_synced_at = now
        await sess.commit()

    logger.info("transparence.synced", target_id=target_id, rpps=rpps,
                fetched=len(rows), added=added)
    return {"status": "resolved", "added": added}
