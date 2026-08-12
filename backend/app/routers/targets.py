import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Target
from app.auth import require_admin

router = APIRouter(prefix="/api/targets", tags=["targets"])


class TargetCreate(BaseModel):
    name: str
    known_urls: list[str] = []
    notes: str | None = None
    disease_area: str | None = None
    target_type: str = "kol"          # 'kol' | 'competitor'
    twitter_handle: str | None = None
    linkedin_url: str | None = None


class TargetUpdate(BaseModel):
    name: str | None = None
    known_urls: list[str] | None = None
    notes: str | None = None
    active: bool | None = None
    disease_area: str | None = None
    target_type: str | None = None
    twitter_handle: str | None = None
    linkedin_url: str | None = None


class TargetOut(BaseModel):
    id: int
    name: str
    known_urls: list[str]
    notes: str | None
    active: bool
    disease_area: str | None = None
    target_type: str = "kol"
    twitter_handle: str | None = None
    linkedin_url: str | None = None

    model_config = {"from_attributes": True}


@router.get("/", response_model=list[TargetOut])
async def list_targets(db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(Target).order_by(Target.name))
    targets = rows.scalars().all()
    result = []
    for t in targets:
        import json
        result.append(TargetOut(
            id=t.id, name=t.name,
            known_urls=json.loads(t.known_urls or "[]"),
            notes=t.notes, active=t.active, disease_area=t.disease_area,
            target_type=t.target_type or "kol",
            twitter_handle=t.twitter_handle, linkedin_url=t.linkedin_url,
        ))
    return result


@router.post("/", response_model=TargetOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_admin)])
async def create_target(body: TargetCreate, db: AsyncSession = Depends(get_db)):
    import json
    if body.target_type not in ("kol", "competitor"):
        raise HTTPException(status_code=422, detail="target_type must be 'kol' or 'competitor'")
    target = Target(
        name=body.name, known_urls=json.dumps(body.known_urls), notes=body.notes,
        disease_area=body.disease_area, target_type=body.target_type,
        twitter_handle=body.twitter_handle, linkedin_url=body.linkedin_url,
    )
    db.add(target)
    await db.commit()
    await db.refresh(target)
    return TargetOut(id=target.id, name=target.name,
                     known_urls=json.loads(target.known_urls or "[]"),
                     notes=target.notes, active=target.active, disease_area=target.disease_area,
                     target_type=target.target_type or "kol",
                     twitter_handle=target.twitter_handle, linkedin_url=target.linkedin_url)


@router.put("/{target_id}", response_model=TargetOut, dependencies=[Depends(require_admin)])
async def update_target(target_id: int, body: TargetUpdate, db: AsyncSession = Depends(get_db)):
    import json
    target = await db.get(Target, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    if body.name is not None:
        target.name = body.name
    if body.known_urls is not None:
        target.known_urls = json.dumps(body.known_urls)
    if body.notes is not None:
        target.notes = body.notes
    if body.active is not None:
        target.active = body.active
    if body.disease_area is not None:
        target.disease_area = body.disease_area
    if body.target_type is not None:
        if body.target_type not in ("kol", "competitor"):
            raise HTTPException(status_code=422, detail="target_type must be 'kol' or 'competitor'")
        target.target_type = body.target_type
    if body.twitter_handle is not None:
        target.twitter_handle = body.twitter_handle or None
    if body.linkedin_url is not None:
        target.linkedin_url = body.linkedin_url or None
    await db.commit()
    await db.refresh(target)
    return TargetOut(id=target.id, name=target.name,
                     known_urls=json.loads(target.known_urls or "[]"),
                     notes=target.notes, active=target.active, disease_area=target.disease_area,
                     target_type=target.target_type or "kol",
                     twitter_handle=target.twitter_handle, linkedin_url=target.linkedin_url)


@router.delete("/{target_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_admin)])
async def deactivate_target(target_id: int, purge: bool = False,
                            db: AsyncSession = Depends(get_db)):
    """Default: soft-deactivate (data kept, target skipped by runs).
    `?purge=true`: permanently remove the target AND its scraped posts,
    insights and summaries — FK order matters, insights reference posts."""
    target = await db.get(Target, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    if not purge:
        target.active = False
        await db.commit()
        return

    from sqlalchemy import delete
    from app.models import ExtractedInsight, PersonSummary, ScrapedPost
    await db.execute(delete(ExtractedInsight).where(ExtractedInsight.target_id == target_id))
    await db.execute(delete(PersonSummary).where(PersonSummary.target_id == target_id))
    await db.execute(delete(ScrapedPost).where(ScrapedPost.target_id == target_id))
    await db.delete(target)
    await db.commit()


# ── KOL module ────────────────────────────────────────────
# The pipeline has always written a PersonSummary per target per run, but until
# now nothing read it back except the PDF generator — the spec's "individual sum
# up for each KOL (with research bar)" had no screen. These endpoints expose
# what was already being generated.

def _bullets(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    out = []
    for item in value if isinstance(value, list) else []:
        text = item.get("text") if isinstance(item, dict) else item
        if isinstance(text, str) and text.strip():
            out.append(text.strip())
    return out


@router.get("/profiles")
async def list_profiles(q: str | None = None, target_type: str = "kol",
                        db: AsyncSession = Depends(get_db)):
    """One card per tracked person: their latest summary plus activity counts.

    Powers the browse/search list. `q` matches the name — the "research bar" in
    the spec — and is applied in SQL so a long roster stays cheap.
    """
    from sqlalchemy import func, or_

    from app.models import ExtractedInsight, PersonSummary, ScrapedPost
    from app.services.ae_filter import post_not_ae

    targets_q = select(Target)
    if target_type and target_type != "all":
        targets_q = targets_q.where(Target.target_type == target_type)
    if q and q.strip():
        targets_q = targets_q.where(Target.name.ilike(f"%{q.strip()}%"))
    targets = (await db.execute(targets_q.order_by(Target.name))).scalars().all()
    if not targets:
        return {"profiles": []}

    ids = [t.id for t in targets]

    # Latest summary per target, chosen by generated_at.
    summaries: dict[int, PersonSummary] = {}
    rows = await db.execute(
        select(PersonSummary)
        .where(PersonSummary.target_id.in_(ids))
        .order_by(PersonSummary.target_id, desc(PersonSummary.generated_at))
    )
    for row in rows.scalars().all():
        summaries.setdefault(row.target_id, row)

    # Insight counts and last activity, AE-filtered (the post is joined, so the
    # column form of the filter is the correct one).
    stats = {
        target_id: (count, last)
        for target_id, count, last in (await db.execute(
            select(ExtractedInsight.target_id, func.count(),
                   func.max(ExtractedInsight.extracted_at))
            .join(ScrapedPost, ExtractedInsight.scraped_post_id == ScrapedPost.id)
            .where(ExtractedInsight.target_id.in_(ids))
            .where(post_not_ae())
            .group_by(ExtractedInsight.target_id)
        )).all()
    }

    profiles = []
    for target in targets:
        summary = summaries.get(target.id)
        count, last = stats.get(target.id, (0, None))
        bullets = _bullets(summary.summary_bullets) if summary else []
        if not bullets and summary:
            bullets = _bullets(summary.summary_bullets_extended)
        profiles.append({
            "id": target.id,
            "name": target.name,
            "target_type": target.target_type,
            "active": target.active,
            "disease_area": target.disease_area,
            "twitter_handle": target.twitter_handle,
            "linkedin_url": target.linkedin_url,
            "insight_count": count,
            "last_activity": last.isoformat() if last else None,
            "summary_bullets": bullets,
            "so_what": (summary.so_what_pharma or summary.so_what_pharma_extended) if summary else None,
            "summary_generated_at": summary.generated_at.isoformat() if summary and summary.generated_at else None,
        })
    return {"profiles": profiles}


@router.get("/{target_id}/profile")
async def get_profile(target_id: int, days: int = 30, db: AsyncSession = Depends(get_db)):
    """Everything known about one person: summary, sentiment split, top topics,
    activity over time, and their recent statements with sources."""
    from collections import Counter
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func

    from app.models import ExtractedInsight, PersonSummary, ScrapedPost
    from app.services.ae_filter import post_not_ae

    target = await db.get(Target, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    window = max(1, min(days, 365))
    since = datetime.now(timezone.utc) - timedelta(days=window)

    summary = (await db.execute(
        select(PersonSummary)
        .where(PersonSummary.target_id == target_id)
        .order_by(desc(PersonSummary.generated_at))
        .limit(1)
    )).scalars().first()

    rows = (await db.execute(
        select(ExtractedInsight, ScrapedPost)
        .join(ScrapedPost, ExtractedInsight.scraped_post_id == ScrapedPost.id)
        .where(ExtractedInsight.target_id == target_id)
        .where(ExtractedInsight.extracted_at >= since)
        .where(post_not_ae())
        .order_by(desc(ExtractedInsight.extracted_at))
        .limit(200)
    )).all()

    sentiment = Counter()
    topics = Counter()
    per_week = Counter()
    statements = []
    for insight, post in rows:
        sentiment[(insight.sentiment or "neutral").lower()] += 1
        if insight.topic:
            topics[insight.topic.strip()] += 1
        when = insight.extracted_at
        if when:
            per_week[(when - timedelta(days=when.weekday())).date().isoformat()] += 1
        if len(statements) < 40:
            statements.append({
                "id": insight.id,
                "topic": insight.topic or "",
                "what_they_said": insight.what_they_said or "",
                "sentiment": insight.sentiment or "neutral",
                "category": insight.category or "",
                "url": post.source_url or "",
                "source_name": post.source_name or post.domain or "",
                "source_scope": post.source_scope or "",
                "date": post.published_date or (
                    insight.extracted_at.date().isoformat() if insight.extracted_at else ""),
            })

    bullets = _bullets(summary.summary_bullets) if summary else []
    if not bullets and summary:
        bullets = _bullets(summary.summary_bullets_extended)

    return {
        "id": target.id,
        "name": target.name,
        "target_type": target.target_type,
        "active": target.active,
        "disease_area": target.disease_area,
        "twitter_handle": target.twitter_handle,
        "linkedin_url": target.linkedin_url,
        "known_urls": json.loads(target.known_urls or "[]"),
        "window_days": window,
        "summary_bullets": bullets,
        "so_what": (summary.so_what_pharma or summary.so_what_pharma_extended) if summary else None,
        "summary_generated_at": summary.generated_at.isoformat() if summary and summary.generated_at else None,
        "insight_count": len(rows),
        "sentiment": dict(sentiment),
        "top_topics": [{"topic": t, "count": c} for t, c in topics.most_common(12)],
        "per_week": dict(sorted(per_week.items())),
        "statements": statements,
    }
