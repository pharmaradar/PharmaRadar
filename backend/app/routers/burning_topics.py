"""Burning Topics — persistent topic tracker with on-demand synthesized reports.

Topics are visible to every logged-in user; anyone can create their own,
edit/delete is owner-or-admin. Report generation enqueues a Celery task
(scrape queue — the TinyFish step needs the big-RAM worker) and the UI polls
the report row until done/failed.
"""
import asyncio
import json
from functools import partial
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import enforce_daily_generation, get_current_user
from app.config import get_settings
from app.database import get_db
from app.models import BurningTopic, BurningTopicReport, User

router = APIRouter(prefix="/api/burning-topics", tags=["burning-topics"])

settings = get_settings()


# ── Schemas ───────────────────────────────────────────────

class TopicCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    description: str | None = None
    language_filter: str | None = None
    period_days: int = Field(default=30, ge=1, le=365)
    exclusion_words: list[str] = []
    restriction_terms: list[str] = []


class TopicUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = None
    language_filter: str | None = None
    period_days: int | None = Field(default=None, ge=1, le=365)
    exclusion_words: list[str] | None = None
    restriction_terms: list[str] | None = None
    is_active: bool | None = None


class FollowupRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    # Prior turns of this follow-up thread, client-held: [{role, content}]
    history: list[dict] = []


def _loads(raw: str | None) -> list:
    try:
        val = json.loads(raw or "[]")
        return val if isinstance(val, list) else []
    except (ValueError, TypeError):
        return []


def _topic_out(t: BurningTopic, latest: BurningTopicReport | None = None) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "language_filter": t.language_filter,
        "period_days": t.period_days,
        "exclusion_words": _loads(t.exclusion_words),
        "restriction_terms": _loads(t.restriction_terms),
        "created_by": t.created_by,
        "is_active": t.is_active,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "latest_report": {
            "id": latest.id,
            "status": latest.status,
            "created_at": latest.created_at.isoformat() if latest.created_at else None,
            "pdf_url": latest.pdf_url,
        } if latest else None,
    }


def _report_out(r: BurningTopicReport) -> dict:
    return {
        "id": r.id,
        "topic_id": r.topic_id,
        "status": r.status,
        "summary_md": r.summary_md,
        "key_findings": _loads(r.key_findings),
        "so_what": r.so_what,
        "important_posts": _loads(r.important_posts),
        "main_authors": _loads(r.main_authors),
        "pdf_url": r.pdf_url,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


async def _get_topic_or_404(topic_id: int, db: AsyncSession) -> BurningTopic:
    topic = await db.get(BurningTopic, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    return topic


def _require_owner_or_admin(topic: BurningTopic, user: User) -> None:
    if user.role != "admin" and topic.created_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the topic owner or an admin can do that")


# ── Topic CRUD ────────────────────────────────────────────

@router.get("/")
async def list_topics(db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    rows = await db.execute(select(BurningTopic).order_by(desc(BurningTopic.created_at)))
    topics = rows.scalars().all()

    # Latest report per topic in one query (small table — fetch and bucket)
    latest_by_topic: dict[int, BurningTopicReport] = {}
    if topics:
        rep_rows = await db.execute(
            select(BurningTopicReport)
            .where(BurningTopicReport.topic_id.in_([t.id for t in topics]))
            .order_by(BurningTopicReport.topic_id, desc(BurningTopicReport.created_at))
        )
        for r in rep_rows.scalars().all():
            latest_by_topic.setdefault(r.topic_id, r)

    return [_topic_out(t, latest_by_topic.get(t.id)) for t in topics]


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_topic(body: TopicCreate, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    topic = BurningTopic(
        name=body.name.strip(),
        description=(body.description or "").strip() or None,
        language_filter=(body.language_filter or "").strip() or None,
        period_days=body.period_days,
        exclusion_words=json.dumps([w.strip() for w in body.exclusion_words if w.strip()]),
        restriction_terms=json.dumps([t.strip() for t in body.restriction_terms if t.strip()]),
        created_by=user.id,
    )
    db.add(topic)
    await db.commit()
    await db.refresh(topic)
    return _topic_out(topic)


@router.put("/{topic_id}")
async def update_topic(topic_id: int, body: TopicUpdate, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    topic = await _get_topic_or_404(topic_id, db)
    _require_owner_or_admin(topic, user)

    if body.name is not None:
        topic.name = body.name.strip()
    if body.description is not None:
        topic.description = body.description.strip() or None
    if body.language_filter is not None:
        topic.language_filter = body.language_filter.strip() or None
    if body.period_days is not None:
        topic.period_days = body.period_days
    if body.exclusion_words is not None:
        topic.exclusion_words = json.dumps([w.strip() for w in body.exclusion_words if w.strip()])
    if body.restriction_terms is not None:
        topic.restriction_terms = json.dumps([t.strip() for t in body.restriction_terms if t.strip()])
    if body.is_active is not None:
        topic.is_active = body.is_active
    await db.commit()
    await db.refresh(topic)
    return _topic_out(topic)


@router.delete("/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_topic(topic_id: int, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    topic = await _get_topic_or_404(topic_id, db)
    _require_owner_or_admin(topic, user)
    await db.delete(topic)   # reports cascade via FK + relationship
    await db.commit()


# ── Report generation + retrieval ─────────────────────────

@router.post("/{topic_id}/generate-report", status_code=status.HTTP_202_ACCEPTED)
async def generate_report(topic_id: int, db: AsyncSession = Depends(get_db),
                          user: User = Depends(get_current_user)):
    topic = await _get_topic_or_404(topic_id, db)
    if not topic.is_active:
        raise HTTPException(status_code=422, detail="Topic is inactive — reactivate it first")

    # One in-flight report per topic
    inflight = await db.execute(
        select(BurningTopicReport.id)
        .where(BurningTopicReport.topic_id == topic_id,
               BurningTopicReport.status.in_(["pending", "running"]))
        .limit(1)
    )
    if inflight.first():
        raise HTTPException(status_code=409, detail="A report for this topic is already in progress")

    # Same daily-quota mechanism as the dashboard briefs: non-admins get one
    # fresh generation per topic per day (existing reports stay readable).
    enforce_daily_generation(user, f"burning_topic:{topic_id}")

    report = BurningTopicReport(topic_id=topic_id, status="pending")
    db.add(report)
    await db.commit()
    await db.refresh(report)

    from app.tasks.burning_topics import generate_topic_report
    generate_topic_report.delay(report.id)

    return {"report_id": report.id, "status": report.status}


@router.get("/{topic_id}/reports")
async def list_reports(topic_id: int, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    await _get_topic_or_404(topic_id, db)
    rows = await db.execute(
        select(BurningTopicReport)
        .where(BurningTopicReport.topic_id == topic_id)
        .order_by(desc(BurningTopicReport.created_at))
        .limit(20)
    )
    return [_report_out(r) for r in rows.scalars().all()]


@router.get("/{topic_id}/reports/{report_id}")
async def get_report(topic_id: int, report_id: int, db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_user)):
    report = await db.get(BurningTopicReport, report_id)
    if not report or report.topic_id != topic_id:
        raise HTTPException(status_code=404, detail="Report not found")
    return _report_out(report)


@router.get("/{topic_id}/reports/{report_id}/pdf")
async def download_report_pdf(topic_id: int, report_id: int, db: AsyncSession = Depends(get_db),
                              user: User = Depends(get_current_user)):
    """Local-file fallback for dev / blob-less setups. In production pdf_url is a
    public Vercel Blob URL and the frontend uses it directly. Path is derived
    from integer IDs only — no user-controlled path segments."""
    report = await db.get(BurningTopicReport, report_id)
    if not report or report.topic_id != topic_id:
        raise HTTPException(status_code=404, detail="Report not found")
    pdf_path = Path(settings.reports_dir) / "burning_topics" / f"topic_{topic_id}_report_{report_id}.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not available for this report")
    return FileResponse(str(pdf_path), media_type="application/pdf", filename=pdf_path.name)


# ── Conversational follow-up on a finished report ─────────

@router.post("/{topic_id}/reports/{report_id}/followup")
async def followup(topic_id: int, report_id: int, body: FollowupRequest,
                   db: AsyncSession = Depends(get_db),
                   user: User = Depends(get_current_user)):
    from app.services.llm_router import call_llm

    topic = await _get_topic_or_404(topic_id, db)
    report = await db.get(BurningTopicReport, report_id)
    if not report or report.topic_id != topic_id:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.status != "done":
        raise HTTPException(status_code=422, detail="Report is not finished yet")

    findings = "\n".join(f"- {f}" for f in _loads(report.key_findings)) or "-"
    posts = "\n".join(
        f"- {p.get('title') or p.get('url')} ({p.get('author') or '?'}, engagement {p.get('engagement', 0)}) — {p.get('why', '')}"
        for p in _loads(report.important_posts)
    ) or "-"
    authors = "\n".join(
        f"- {a.get('author')}: {a.get('posts', 0)} post(s), engagement {a.get('engagement', 0)}"
        for a in _loads(report.main_authors)
    ) or "-"

    system_prompt = (
        "You are Hermes AI, PharmaRadar's pharma-intelligence analyst. The user is asking "
        "follow-up questions about one burning-topic report. Ground every answer strictly in "
        "the report content below; when the report doesn't cover something, say so briefly "
        "instead of inventing. Be concise and specific."
    )
    report_context = (
        f"BURNING TOPIC REPORT — {topic.name} (last {topic.period_days} days)\n\n"
        f"SUMMARY:\n{report.summary_md or '-'}\n\n"
        f"KEY FINDINGS:\n{findings}\n\n"
        f"SO WHAT:\n{report.so_what or '-'}\n\n"
        f"IMPORTANT POSTS:\n{posts}\n\n"
        f"MAIN AUTHORS:\n{authors}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": report_context},
    ]
    for h in body.history[-10:]:
        if h.get("role") in ("user", "assistant") and (h.get("content") or "").strip():
            messages.append({"role": h["role"], "content": str(h["content"])[:4000]})
    messages.append({"role": "user", "content": body.question})

    # call_llm is synchronous (and loads settings via its own event loop) —
    # run in a thread pool, same as routers/agent.py
    loop = asyncio.get_event_loop()
    try:
        answer = await loop.run_in_executor(None, partial(call_llm, messages, max_tokens=2048))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {str(exc)[:200]}")

    return {"answer": answer}
