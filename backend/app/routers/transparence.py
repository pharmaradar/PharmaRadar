"""Transparence Santé endpoints — French Sunshine Act payments.

Every response carries the identity the figures rest on and when they were last
synced. That is not decoration: a payment total is only meaningful if the reader
knows whose it is and how current it is, and the whole point of pinning to an
RPPS is lost if the UI cannot show that it happened.

Targets that did not resolve confidently return their reason and NO figures.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, require_admin
from app.database import get_db
from app.models import Target, User
from app.models.transparence import TransparencePayment
from app.services.transparence import company_key_sql

router = APIRouter(prefix="/api/transparence", tags=["transparence"])


def _identity(target: Target) -> dict:
    """The provenance block attached to every response."""
    return {
        "target_id": target.id,
        "target_name": target.name,
        "status": target.transparence_status,
        "rpps": target.transparence_rpps,
        "confidence": target.transparence_confidence,
        "note": target.transparence_note,
        "resolved_at": (target.transparence_resolved_at.isoformat()
                        if target.transparence_resolved_at else None),
        "synced_at": (target.transparence_synced_at.isoformat()
                      if target.transparence_synced_at else None),
    }


@router.get("/target/{target_id}")
async def target_payments(target_id: int, limit: int = 25,
                          db: AsyncSession = Depends(get_db),
                          user: User = Depends(get_current_user)):
    """Who pays this KOL, how much, and for what.

    Companies are aggregated by SIREN rather than by the filed trade name — see
    services/transparence.company_key_sql for why that distinction changes the
    answer rather than merely tidying it.
    """
    target = await db.get(Target, target_id)
    if not target:
        raise HTTPException(404, "Target not found")

    identity = _identity(target)

    # Unresolved / ambiguous / not-found targets return the reason and nothing
    # else. Showing a number here is exactly the failure this feature is
    # designed to make impossible.
    if target.transparence_status != "resolved" or not target.transparence_rpps:
        return {**identity, "companies": [], "recent": [],
                "total_eur": 0.0, "payment_count": 0, "displayable": False}

    key = company_key_sql()
    rows = (await db.execute(text(f"""
        SELECT {key}                       AS company_key,
               MAX(company)                AS company,
               MAX(company_siren)          AS siren,
               COUNT(*)                    AS payments,
               SUM(amount_eur)             AS total_eur,
               MIN(paid_on)                AS first_paid,
               MAX(paid_on)                AS last_paid
        FROM transparence_payments
        WHERE target_id = :tid
        GROUP BY company_key
        ORDER BY total_eur DESC
        LIMIT :lim
    """), {"tid": target_id, "lim": limit})).all()

    companies = [{
        "company": r.company,
        "siren": r.siren,
        "payments": r.payments,
        "total_eur": round(float(r.total_eur or 0), 2),
        "first_paid": r.first_paid.isoformat() if r.first_paid else None,
        "last_paid": r.last_paid.isoformat() if r.last_paid else None,
    } for r in rows]

    totals = (await db.execute(
        select(func.count(TransparencePayment.id),
               func.coalesce(func.sum(TransparencePayment.amount_eur), 0.0))
        .where(TransparencePayment.target_id == target_id))).one()

    recent = (await db.execute(
        select(TransparencePayment)
        .where(TransparencePayment.target_id == target_id)
        .order_by(TransparencePayment.paid_on.desc().nullslast())
        .limit(10))).scalars().all()

    return {
        **identity,
        "displayable": True,
        "payment_count": totals[0],
        "total_eur": round(float(totals[1] or 0), 2),
        "companies": companies,
        "recent": [{
            "company": p.company,
            "amount_eur": p.amount_eur,
            "paid_on": p.paid_on.isoformat() if p.paid_on else None,
            "reason": p.reason,
            "kind": p.kind,
        } for p in recent],
    }


@router.get("/overview")
async def overview(db: AsyncSession = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """Share of industry investment across every resolved KOL.

    The headline competitive-intelligence view: which companies are funding the
    KOLs we track, ranked by euros.
    """
    key = company_key_sql()
    rows = (await db.execute(text(f"""
        SELECT {key}            AS company_key,
               MAX(company)     AS company,
               MAX(company_siren) AS siren,
               COUNT(*)         AS payments,
               COUNT(DISTINCT target_id) AS kols,
               SUM(amount_eur)  AS total_eur
        FROM transparence_payments
        GROUP BY company_key
        ORDER BY total_eur DESC
        LIMIT 20
    """))).all()

    coverage = (await db.execute(text("""
        SELECT transparence_status, COUNT(*) AS n
        FROM targets WHERE active IS TRUE AND target_type = 'kol'
        GROUP BY 1
    """))).all()

    grand_total = sum(float(r.total_eur or 0) for r in rows)

    return {
        "companies": [{
            "company": r.company,
            "siren": r.siren,
            "payments": r.payments,
            "kols": r.kols,
            "total_eur": round(float(r.total_eur or 0), 2),
            # Share of what we can see, which is not share of their whole
            # French budget — labelled as such in the UI.
            "share_pct": (round(float(r.total_eur or 0) / grand_total * 100, 1)
                          if grand_total else 0.0),
        } for r in rows],
        "total_eur": round(grand_total, 2),
        "coverage": {r.transparence_status: r.n for r in coverage},
    }


@router.post("/sync", dependencies=[Depends(require_admin)])
async def trigger_sync(target_id: int | None = None, force: bool = False):
    """Run the sync now instead of waiting for the 04:10 beat."""
    from app.tasks.transparence import sync_transparence

    task = sync_transparence.delay([target_id] if target_id else None, force)
    return {"queued": True, "task_id": task.id,
            "scope": f"target {target_id}" if target_id else "all active targets"}
