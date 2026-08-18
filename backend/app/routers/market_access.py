"""French market-access events — HAS added-benefit rulings, ANSM shortages."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, require_admin
from app.database import get_db
from app.models import User

router = APIRouter(prefix="/api/market-access", tags=["market-access"])

# ASMR runs I (major added benefit) to V (none). Roman numerals sort wrong as
# text and mean nothing to a reader outside France, so the UI gets both a rank
# for ordering and the French wording HAS itself uses.
ASMR_MEANING = {
    "I":   ("Majeure", 1),
    "II":  ("Importante", 2),
    "III": ("Modérée", 3),
    "IV":  ("Mineure", 4),
    "V":   ("Inexistante", 5),
}


@router.get("/events")
async def events(days: int = Query(1825, ge=1, le=7300),
                 owner: str | None = None,
                 kind: str | None = None,
                 limit: int = Query(60, ge=1, le=300),
                 db: AsyncSession = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """Rulings and shortages on tracked drugs, newest first.

    One HAS opinion covers every presentation of a drug — Tecentriq 840mg,
    1200mg, 1875mg — and the register files a row per presentation. Shown raw
    that reads as the same ruling repeated three times, so identical rulings are
    collapsed and the presentation count is carried instead.
    """
    since = date.today() - timedelta(days=days)
    filters = ["event_date >= :since"]
    params: dict = {"since": since, "lim": limit}
    if owner:
        filters.append("owner = :owner")
        params["owner"] = owner
    if kind:
        filters.append("kind = :kind")
        params["kind"] = kind
    where = " AND ".join(filters)

    rows = (await db.execute(text(f"""
        SELECT kind, brand, owner, rating, opinion_ref, event_date, end_date,
               MAX(url)                    AS url,
               MAX(summary)                AS summary,
               MAX(holder)                 AS holder,
               COUNT(*)                    AS presentations,
               MIN(drug_name)              AS drug_name
        FROM market_access_events
        WHERE {where}
        GROUP BY kind, brand, owner, rating, opinion_ref, event_date, end_date
        ORDER BY event_date DESC
        LIMIT :lim
    """), params)).all()

    out = []
    for r in rows:
        label, rank = ASMR_MEANING.get((r.rating or "").strip(), (None, None))
        out.append({
            "kind": r.kind,
            "brand": r.brand,
            "owner": r.owner,
            "is_ours": r.owner == "roche",
            "rating": r.rating,
            "rating_label": label,
            "rating_rank": rank,
            "opinion_ref": r.opinion_ref,
            "event_date": r.event_date.isoformat() if r.event_date else None,
            "end_date": r.end_date.isoformat() if r.end_date else None,
            "url": r.url,
            "summary": r.summary,
            "holder": r.holder,
            "presentations": r.presentations,
            "drug_name": r.drug_name,
        })
    return {"events": out, "window_days": days}


@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """Added-benefit ratings by owner — how France has judged us against them."""
    rows = (await db.execute(text("""
        SELECT owner, rating, COUNT(DISTINCT COALESCE(opinion_ref, cis_code)) AS n
        FROM market_access_events
        WHERE kind = 'asmr' AND rating IS NOT NULL
        GROUP BY owner, rating
    """))).all()

    by_owner: dict[str, dict] = {}
    for r in rows:
        entry = by_owner.setdefault(r.owner, {"owner": r.owner, "is_ours": r.owner == "roche",
                                              "ratings": {}, "total": 0})
        entry["ratings"][r.rating] = r.n
        entry["total"] += r.n

    latest = (await db.execute(text("""
        SELECT MAX(event_date) AS d, MAX(synced_at) AS s FROM market_access_events
    """))).one()

    return {
        "owners": sorted(by_owner.values(), key=lambda o: o["total"], reverse=True),
        "rating_meaning": {k: {"label": v[0], "rank": v[1]} for k, v in ASMR_MEANING.items()},
        "latest_event": latest.d.isoformat() if latest.d else None,
        "synced_at": latest.s.isoformat() if latest.s else None,
    }


@router.post("/sync", dependencies=[Depends(require_admin)])
async def trigger_sync():
    from app.tasks.market_access import sync_market_access
    task = sync_market_access.delay()
    return {"queued": True, "task_id": task.id}
