"""Congress tracking and question-driven report generation."""
import json
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import enforce_daily_generation, get_current_user
from app.config import get_settings
from app.database import get_db
from app.models import BurningTopicReport, Congress, CongressQuestion, User
from app.routers.burning_topics import _loads, _report_out

router = APIRouter(prefix="/api/congress", tags=["congress"])
settings = get_settings()


class CongressCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    hashtags: list[str] = Field(default_factory=list)
    start_date: date
    end_date: date
    disease_area: str | None = Field(default=None, max_length=128)


class CongressUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    hashtags: list[str] | None = None
    start_date: date | None = None
    end_date: date | None = None
    disease_area: str | None = Field(default=None, max_length=128)
    is_active: bool | None = None


class QuestionCreate(BaseModel):
    question_text: str = Field(min_length=2, max_length=2000)


class QuestionUpdate(BaseModel):
    question_text: str = Field(min_length=2, max_length=2000)


def _clean_list(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in values if v and v.strip()))


def _validate_dates(start_date: date, end_date: date) -> None:
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _question_out(q: CongressQuestion) -> dict:
    return {
        "id": q.id,
        "congress_id": q.congress_id,
        "question_text": q.question_text,
        "created_at": _iso(q.created_at),
    }


def _congress_out(c: Congress, questions: list[CongressQuestion], latest=None) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "hashtags": _loads(c.hashtags),
        "start_date": _iso(c.start_date),
        "end_date": _iso(c.end_date),
        "disease_area": c.disease_area,
        "is_active": c.is_active,
        "created_at": _iso(c.created_at),
        "questions": [_question_out(q) for q in questions],
        "latest_report": {
            "id": latest.id,
            "status": latest.status,
            "created_at": _iso(latest.created_at),
            "pdf_url": latest.pdf_url,
        } if latest else None,
    }


async def _get_congress_or_404(congress_id: int, db: AsyncSession) -> Congress:
    congress = await db.get(Congress, congress_id)
    if not congress:
        raise HTTPException(status_code=404, detail="Congress not found")
    return congress


async def _get_questions(congress_id: int, db: AsyncSession) -> list[CongressQuestion]:
    rows = await db.execute(
        select(CongressQuestion)
        .where(CongressQuestion.congress_id == congress_id)
        .order_by(CongressQuestion.created_at, CongressQuestion.id)
    )
    return list(rows.scalars().all())


async def _latest_reports(congress_ids: list[int], db: AsyncSession) -> dict[int, object]:
    """Latest report per congress via DISTINCT ON — only the 4 fields the list
    UI needs, instead of every historical report row with its text blobs."""
    if not congress_ids:
        return {}
    rows = await db.execute(
        select(BurningTopicReport.congress_id, BurningTopicReport.id,
               BurningTopicReport.status, BurningTopicReport.created_at,
               BurningTopicReport.pdf_url)
        .where(BurningTopicReport.congress_id.in_(congress_ids))
        .distinct(BurningTopicReport.congress_id)
        .order_by(BurningTopicReport.congress_id, desc(BurningTopicReport.created_at))
    )
    return {r.congress_id: r for r in rows.all()}


@router.get("/")
async def list_congresses(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    rows = await db.execute(select(Congress).order_by(desc(Congress.start_date), desc(Congress.created_at)))
    congresses = list(rows.scalars().all())
    latest = await _latest_reports([c.id for c in congresses], db)
    questions: dict[int, list[CongressQuestion]] = {c.id: [] for c in congresses}
    if congresses:
        qrows = await db.execute(
            select(CongressQuestion)
            .where(CongressQuestion.congress_id.in_([c.id for c in congresses]))
            .order_by(CongressQuestion.created_at, CongressQuestion.id)
        )
        for question in qrows.scalars().all():
            questions[question.congress_id].append(question)
    return [_congress_out(c, questions[c.id], latest.get(c.id)) for c in congresses]


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_congress(body: CongressCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    _validate_dates(body.start_date, body.end_date)
    congress = Congress(
        name=body.name.strip(),
        hashtags=json.dumps(_clean_list(body.hashtags)),
        start_date=body.start_date,
        end_date=body.end_date,
        disease_area=(body.disease_area or "").strip() or None,
    )
    db.add(congress)
    await db.commit()
    await db.refresh(congress)
    return _congress_out(congress, [])


@router.get("/{congress_id}")
async def get_congress(congress_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    congress = await _get_congress_or_404(congress_id, db)
    reports = await _latest_reports([congress_id], db)
    return _congress_out(congress, await _get_questions(congress_id, db), reports.get(congress_id))


@router.put("/{congress_id}")
async def update_congress(congress_id: int, body: CongressUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    congress = await _get_congress_or_404(congress_id, db)
    start_date = body.start_date or congress.start_date
    end_date = body.end_date or congress.end_date
    _validate_dates(start_date, end_date)
    if body.name is not None:
        congress.name = body.name.strip()
    if body.hashtags is not None:
        congress.hashtags = json.dumps(_clean_list(body.hashtags))
    if body.start_date is not None:
        congress.start_date = body.start_date
    if body.end_date is not None:
        congress.end_date = body.end_date
    if body.disease_area is not None:
        congress.disease_area = body.disease_area.strip() or None
    if body.is_active is not None:
        congress.is_active = body.is_active
    await db.commit()
    await db.refresh(congress)
    latest = (await _latest_reports([congress_id], db)).get(congress_id)
    return _congress_out(congress, await _get_questions(congress_id, db), latest)


@router.delete("/{congress_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_congress(congress_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    congress = await _get_congress_or_404(congress_id, db)
    await db.delete(congress)
    await db.commit()


@router.get("/{congress_id}/questions")
async def list_questions(congress_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await _get_congress_or_404(congress_id, db)
    return [_question_out(q) for q in await _get_questions(congress_id, db)]


@router.post("/{congress_id}/questions", status_code=status.HTTP_201_CREATED)
async def create_question(congress_id: int, body: QuestionCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await _get_congress_or_404(congress_id, db)
    question = CongressQuestion(congress_id=congress_id, question_text=body.question_text.strip())
    db.add(question)
    await db.commit()
    await db.refresh(question)
    return _question_out(question)


async def _get_question_or_404(congress_id: int, question_id: int, db: AsyncSession) -> CongressQuestion:
    question = await db.get(CongressQuestion, question_id)
    if not question or question.congress_id != congress_id:
        raise HTTPException(status_code=404, detail="Question not found")
    return question


@router.put("/{congress_id}/questions/{question_id}")
async def update_question(congress_id: int, question_id: int, body: QuestionUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    question = await _get_question_or_404(congress_id, question_id, db)
    question.question_text = body.question_text.strip()
    await db.commit()
    await db.refresh(question)
    return _question_out(question)


@router.delete("/{congress_id}/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question(congress_id: int, question_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    question = await _get_question_or_404(congress_id, question_id, db)
    await db.delete(question)
    await db.commit()


@router.post("/{congress_id}/generate-report", status_code=status.HTTP_202_ACCEPTED)
async def generate_report(congress_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    congress = await _get_congress_or_404(congress_id, db)
    if not congress.is_active:
        raise HTTPException(status_code=422, detail="Congress is inactive — reactivate it first")
    inflight = await db.execute(
        select(BurningTopicReport.id)
        .where(BurningTopicReport.congress_id == congress_id,
               BurningTopicReport.status.in_(["pending", "running"]))
        .limit(1)
    )
    if inflight.first():
        raise HTTPException(status_code=409, detail="A report for this congress is already in progress")

    enforce_daily_generation(user, f"congress:{congress_id}")
    report = BurningTopicReport(congress_id=congress_id, status="pending")
    db.add(report)
    await db.commit()
    await db.refresh(report)

    from app.tasks.burning_topics import generate_topic_report
    generate_topic_report.delay(report.id)
    return {"report_id": report.id, "status": report.status}


@router.get("/{congress_id}/reports")
async def list_reports(congress_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await _get_congress_or_404(congress_id, db)
    rows = await db.execute(
        select(BurningTopicReport)
        .where(BurningTopicReport.congress_id == congress_id)
        .order_by(desc(BurningTopicReport.created_at))
        .limit(20)
    )
    return [_report_out(r) for r in rows.scalars().all()]


@router.get("/{congress_id}/reports/{report_id}")
async def get_report(congress_id: int, report_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    report = await db.get(BurningTopicReport, report_id)
    if not report or report.congress_id != congress_id:
        raise HTTPException(status_code=404, detail="Report not found")
    return _report_out(report)


@router.get("/{congress_id}/reports/{report_id}/pdf")
async def download_report_pdf(congress_id: int, report_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    report = await db.get(BurningTopicReport, report_id)
    if not report or report.congress_id != congress_id:
        raise HTTPException(status_code=404, detail="Report not found")
    pdf_path = Path(settings.reports_dir) / "congresses" / f"congress_{congress_id}_report_{report_id}.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not available for this report")
    return FileResponse(str(pdf_path), media_type="application/pdf", filename=pdf_path.name)
