"""Reports endpoint — backed by Vercel Blob storage (single source of truth).

The previous implementation walked `settings.reports_dir` on the local
filesystem. That broke in production because the WORKER writes PDFs to
its own `/tmp/reports` and uploads them to Vercel Blob, while the BACKEND
container's `/tmp/reports` stays empty — the two services don't share a
filesystem on Railway. Listing/downloading must go through Blob.
"""
import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models import ExtractedInsight, Target
from app.services.vercel_blob_storage import run_blob_op

router = APIRouter(prefix="/api/reports", tags=["reports"])
settings = get_settings()


async def _blob_call(fn):
    """Run a synchronous `vercel_blob` call without blocking the event loop.

    The blob SDK does blocking HTTP; run it in the thread pool (run_blob_op
    also makes the token env-var handling thread-safe)."""
    if not settings.vercel_blob_token:
        return None
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: run_blob_op(fn, settings.vercel_blob_token)
    )


@router.get("/")
async def list_pdfs() -> list[dict[str, Any]]:
    """List all PDF reports — from Vercel Blob in production, local filesystem locally.

    Covers every report family: pipeline reports (reports/), burning-topic +
    congress reports (burning-topics/) and global syntheses (global-synthesis/)."""
    if not settings.vercel_blob_token:
        return _list_local_pdfs()

    import vercel_blob

    blobs: list[dict] = []
    for prefix in ("reports/", "burning-topics/", "global-synthesis/"):
        result = await _blob_call(lambda p=prefix: vercel_blob.list({"prefix": p, "limit": 1000}))
        if isinstance(result, dict):
            blobs.extend(result.get("blobs", []))
    pdfs = [b for b in blobs if b.get("pathname", "").endswith(".pdf")]
    pdfs.sort(key=lambda b: b.get("uploadedAt") or "", reverse=True)
    return [
        {
            "path": b["pathname"],
            "name": b["pathname"].rsplit("/", 1)[-1],
            "size": b.get("size", 0),
            "url": b.get("url", ""),
            "uploadedAt": b.get("uploadedAt"),
        }
        for b in pdfs
    ]


def _list_local_pdfs() -> list[dict[str, Any]]:
    from pathlib import Path
    reports_dir = Path(settings.reports_dir)
    if not reports_dir.exists():
        return []
    pdfs = sorted(reports_dir.rglob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    result = []
    for p in pdfs:
        rel = p.relative_to(reports_dir)
        pathname = f"reports/{rel.as_posix()}"
        result.append({
            "path": pathname,
            "name": p.name,
            "size": p.stat().st_size,
            "url": f"/api/reports/local/{rel.as_posix()}",
            "uploadedAt": None,
        })
    return result


@router.get("/latest")
async def latest_insights(limit: int = 20, db: AsyncSession = Depends(get_db)):
    """Return most recent extracted insights across all targets, sorted by post published date."""
    from app.models import ScrapedPost
    from app.services.ae_filter import post_not_ae
    from sqlalchemy import nulls_last
    rows = await db.execute(
        select(ExtractedInsight, Target, ScrapedPost)
        .join(Target, ExtractedInsight.target_id == Target.id)
        .join(ScrapedPost, ExtractedInsight.scraped_post_id == ScrapedPost.id)
        .where(post_not_ae())
        .order_by(nulls_last(desc(ScrapedPost.published_date)))
        .limit(limit)
    )
    return [
        {
            "id": ins.id,
            "target_name": target.name,
            "topic": ins.topic,
            "what_they_said": ins.what_they_said,
            "sentiment": ins.sentiment,
            "category": ins.category,
            "extracted_at": ins.extracted_at.isoformat(),
            "source_url": post.source_url or None,
            "source_name": post.source_name or None,
            "published_date": post.published_date or None,
        }
        for ins, target, post in rows.all()
    ]


# ── Global synthesis (dashboard) ──────────────────────────

@router.post("/global-synthesis", status_code=202)
async def trigger_global_synthesis(user=Depends(get_current_user)):
    """Enqueue the global synthesis (KOL brief + population brief + burning-topic
    reports merged in one LLM pass). Non-admins: one fresh generation per day —
    the stored result stays readable for free."""
    import json

    from app.auth import enforce_daily_generation
    from app.tasks.llm import (GLOBAL_SYNTH_STATUS_KEY, generate_global_synthesis,
                               set_global_synth_status, _gs_redis)

    try:
        raw = _gs_redis().get(GLOBAL_SYNTH_STATUS_KEY)
        if raw and json.loads(raw).get("status") == "running":
            raise HTTPException(status_code=409, detail="A global synthesis is already running")
    except HTTPException:
        raise
    except Exception:
        pass

    enforce_daily_generation(user, "global_synthesis")
    set_global_synth_status(status="running")
    generate_global_synthesis.delay()
    return {"status": "running"}


@router.get("/global-synthesis")
async def get_global_synthesis(user=Depends(get_current_user)):
    """Status + last stored result. The result persists until the next
    generation replaces it (dashboard shows it without regenerating)."""
    import json

    from app.tasks.llm import GLOBAL_SYNTH_RESULT_KEY, GLOBAL_SYNTH_STATUS_KEY, _gs_redis

    status: dict = {"status": "idle"}
    result = None
    try:
        r = _gs_redis()
        raw = r.get(GLOBAL_SYNTH_STATUS_KEY)
        if raw:
            status = json.loads(raw)
        raw = r.get(GLOBAL_SYNTH_RESULT_KEY)
        if raw:
            result = json.loads(raw)
    except Exception:
        pass
    if status.get("status") == "running" and result is not None:
        # keep showing the previous result while a new one cooks
        pass
    return {"status": status.get("status", "idle"), "error": status.get("error"), "result": result}


# ── Dashboard syntheses (KOL / competitor / comprehensive) ─

@router.post("/synthesis/{scope}", status_code=202)
async def trigger_synthesis(scope: str, user=Depends(get_current_user)):
    """Enqueue one of the three dashboard synthesis PDFs.

    Non-admins get one fresh generation per scope per day; the stored report
    stays readable for free in between.
    """
    from app.auth import enforce_daily_generation
    from app.services import synthesis_report as sr
    from app.tasks.synthesis import generate_synthesis_report

    try:
        sr.spec_for(scope)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # Staleness-aware: a status left at "running" by a worker that died must not
    # block regeneration until the 24h key expires.
    if sr.is_running(scope):
        raise HTTPException(status_code=409, detail=f"A {scope} synthesis is already running")

    enforce_daily_generation(user, f"synthesis_{scope}")
    sr.set_status(scope, status="running")
    generate_synthesis_report.delay(scope)
    return {"status": "running", "scope": scope}


@router.get("/synthesis/{scope}")
async def get_synthesis(scope: str, user=Depends(get_current_user)):
    """Status plus the last stored report for one scope."""
    from app.services import synthesis_report as sr

    try:
        sr.spec_for(scope)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"scope": scope, **sr.get_state(scope)}


@router.get("/local/{file_path:path}")
async def serve_local_pdf(file_path: str):
    """Serve a PDF directly from the local filesystem (dev only)."""
    from pathlib import Path
    from fastapi.responses import FileResponse
    base = Path(settings.reports_dir).resolve()
    pdf_path = (base / file_path).resolve()
    # Reject ../ traversal — only files inside the reports dir are servable
    if not pdf_path.is_relative_to(base):
        raise HTTPException(status_code=404, detail="File not found")
    if not pdf_path.exists() or not pdf_path.suffix == ".pdf":
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(pdf_path), media_type="application/pdf")


@router.get("/download/{file_path:path}")
async def download_pdf(file_path: str, inline: bool = False):
    """Redirect to the public Vercel Blob URL for the requested PDF.

    The store is public, so the blob URL is directly fetchable by the browser.
    We look up the URL via vercel_blob.head() so we don't have to hard-code the
    store-id-derived hostname.
    """
    import vercel_blob

    try:
        result = await _blob_call(lambda: vercel_blob.head(file_path))
    except Exception:
        result = None
    if not result:
        raise HTTPException(status_code=404, detail="File not found")

    url = result.get("downloadUrl") if not inline else result.get("url")
    url = url or result.get("url")
    if not url:
        raise HTTPException(status_code=404, detail="File not found")
    return RedirectResponse(url=url, status_code=302)


# ── Single-insight analysis ───────────────────────────────

async def _load_insight(db: AsyncSession, insight_id: int):
    """The insight with the target that said it and the post it came from."""
    from app.models import ScrapedPost

    row = (await db.execute(
        select(ExtractedInsight, Target, ScrapedPost)
        .join(Target, ExtractedInsight.target_id == Target.id)
        .outerjoin(ScrapedPost, ExtractedInsight.scraped_post_id == ScrapedPost.id)
        .where(ExtractedInsight.id == insight_id)
    )).first()
    if not row:
        raise HTTPException(status_code=404, detail="Insight not found")
    return row


@router.get("/insight/{insight_id}/analysis")
async def get_insight_analysis(insight_id: int, db: AsyncSession = Depends(get_db),
                               user=Depends(get_current_user)):
    """Whatever analysis is stored. Never calls the LLM, so opening is free."""
    import json

    insight, _target, _post = await _load_insight(db, insight_id)
    if not insight.analysis_sections:
        return {"sections": None, "cached": False}
    try:
        return {"sections": json.loads(insight.analysis_sections), "cached": True}
    except Exception:                               # noqa: BLE001
        return {"sections": None, "cached": False}


@router.post("/insight/{insight_id}/analyse")
async def analyse_insight(insight_id: int, refresh: bool = False,
                          db: AsyncSession = Depends(get_db),
                          user=Depends(get_current_user)):
    """Analyse one KOL or competitor statement in the standard report format."""
    import json
    from datetime import datetime, timezone

    insight, target, post = await _load_insight(db, insight_id)

    if insight.analysis_sections and not refresh:
        try:
            return {"sections": json.loads(insight.analysis_sections), "cached": True}
        except Exception:                           # noqa: BLE001 - rewrite bad cache
            pass

    statement = (insight.what_they_said or "").strip()
    if not statement:
        raise HTTPException(422, "This insight has no statement to analyse")

    # Voice is EXACT here, unlike a social post: the row is joined to a tracked
    # Target, so we know both who spoke and in what capacity. No classifier
    # guessing, and no confidence caveat to render.
    voice = {
        "bucket": "kol" if target.target_type == "kol" else "organisation",
        "confidence": "exact",
        "evidence": f"tracked {target.target_type or 'target'}: {target.name}",
        "name": target.name,
        "target_type": target.target_type,
    }

    # Web articles carry no engagement — measured 0 of 27 scraped posts have any.
    # Reporting zeros would read as "nobody engaged" rather than "not measurable".
    engagement = (getattr(post, "likes", None) or 0) + (getattr(post, "views", None) or 0)
    reach = {
        "available": engagement > 0,
        "likes": getattr(post, "likes", None) or 0,
        "views": getattr(post, "views", None) or 0,
        "engagement": engagement,
        "source_name": getattr(post, "source_name", None) or insight.__dict__.get("source_name"),
        "note": (None if engagement > 0 else
                 "This came from a web article rather than a social platform, so "
                 "engagement figures are not available."),
    }

    prompt = (
        "You are a pharma intelligence analyst monitoring the French market for "
        "Roche. Analyse this SINGLE statement from a tracked "
        f"{'KOL' if target.target_type == 'kol' else 'competitor'}.\n\n"
        "Output EXACTLY these sections, each starting with its marker:\n\n"
        "##EXEC_SUMMARY##\n2-3 sentences: what was said, by whom, in what "
        "context.\n\n"
        "##SO_WHAT##\n2-3 sentences on the strategic implication for a pharma "
        "medical affairs team — the signal, opportunity or risk, and what to do "
        "about it. Be specific and actionable.\n\n"
        "##WHAT_IS_SAID##\n2-4 sentences on the substance: the actual claims, "
        "products, trials or positions stated.\n\n"
        "##VOICE##\n1-2 sentences on who is speaking and what standing they "
        f"carry. This is a tracked {target.target_type or 'target'} named "
        f"{target.name} — treat that as established fact, not inference.\n\n"
        "##REACH##\n1-2 sentences on how far this is likely to travel and who "
        "sees it. "
        + ("Engagement figures are NOT available for this source — say so "
           "plainly and do not invent numbers.\n\n" if not reach["available"]
           else f"Engagement: {reach['likes']} likes, {reach['views']} views.\n\n")
        + "##SUBTOPICS##\n3-5 lines starting '- ': the themes worth tracking "
        "from this statement.\n\n"
        "Base every statement on the material. Do not speculate beyond it.\n\n"
        f"Speaker: {target.name} ({target.target_type})\n"
        f"Topic: {insight.topic or '-'}\n"
        f"Category: {insight.category or '-'}\n"
        f"Sentiment recorded: {insight.sentiment or '-'}\n"
        f"Published: {getattr(post, 'published_date', None) or '-'}\n\n"
        f"What they said:\n{statement[:4000]}\n\n"
        f"Context:\n{(insight.context or '')[:2000]}"
    )

    from app.services.llm_router import call_llm_async
    try:
        # gemini-2.5-flash counts reasoning against this budget; a small cap
        # truncates the tail sections silently.
        reply = await call_llm_async([{"role": "user", "content": prompt}], max_tokens=6000)
    except Exception as exc:                        # noqa: BLE001
        raise HTTPException(502, f"LLM call failed: {str(exc)[:200]}") from exc

    from app.services.synthesizer import extract_section, parse_bullets, trim_incomplete

    raw = reply or ""
    sections = {
        "exec_summary": trim_incomplete(extract_section(raw, "EXEC_SUMMARY")),
        "so_what": trim_incomplete(extract_section(raw, "SO_WHAT")),
        "what_is_said": trim_incomplete(extract_section(raw, "WHAT_IS_SAID")),
        "voice_note": trim_incomplete(extract_section(raw, "VOICE")),
        "reach_note": trim_incomplete(extract_section(raw, "REACH")),
        "subtopics": parse_bullets(extract_section(raw, "SUBTOPICS")),
        "voice": voice,
        "reach": reach,
    }
    insight.analysis_sections = json.dumps(sections)
    insight.analysed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"sections": sections, "cached": False}


@router.post("/syntheses/refresh", status_code=202)
async def refresh_all_syntheses_now(user=Depends(get_current_user)):
    """Regenerate every dashboard synthesis in one action.

    The alternative is pressing Generate on each artefact in turn and hoping
    none was missed — which is how a dashboard ends up mixing analyses written
    days apart over the same corpus.
    """
    from app.auth import enforce_daily_generation
    from app.tasks.synthesis import refresh_all_syntheses

    # One LLM run per scope, so it draws on the same daily quota as generating
    # them individually would have.
    enforce_daily_generation(user, "synthesis_refresh")
    try:
        task = refresh_all_syntheses.delay("manual")
    except Exception as exc:                        # noqa: BLE001
        raise HTTPException(503, f"queue unavailable: {str(exc)[:120]}") from exc
    return {"queued": True, "task_id": task.id}
