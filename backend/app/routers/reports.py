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
