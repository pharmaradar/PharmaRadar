"""Reports endpoint — backed by Vercel Blob storage (single source of truth).

The previous implementation walked `settings.reports_dir` on the local
filesystem. That broke in production because the WORKER writes PDFs to
its own `/tmp/reports` and uploads them to Vercel Blob, while the BACKEND
container's `/tmp/reports` stays empty — the two services don't share a
filesystem on Railway. Listing/downloading must go through Blob.
"""
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import ExtractedInsight, Target

router = APIRouter(prefix="/api/reports", tags=["reports"])
settings = get_settings()


def _with_blob_token(fn):
    """Run a `vercel_blob` call with BLOB_READ_WRITE_TOKEN set from settings."""
    if not settings.vercel_blob_token:
        return None
    previous = os.environ.get("BLOB_READ_WRITE_TOKEN")
    os.environ["BLOB_READ_WRITE_TOKEN"] = settings.vercel_blob_token
    try:
        return fn()
    finally:
        if previous is None:
            os.environ.pop("BLOB_READ_WRITE_TOKEN", None)
        else:
            os.environ["BLOB_READ_WRITE_TOKEN"] = previous


@router.get("/")
async def list_pdfs() -> list[dict[str, Any]]:
    """List all PDF reports — from Vercel Blob in production, local filesystem locally."""
    if not settings.vercel_blob_token:
        return _list_local_pdfs()

    import vercel_blob

    result = _with_blob_token(lambda: vercel_blob.list({"prefix": "reports/", "limit": 1000}))
    if not result:
        return []

    blobs = result.get("blobs", []) if isinstance(result, dict) else []
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
    from sqlalchemy import nulls_last
    rows = await db.execute(
        select(ExtractedInsight, Target, ScrapedPost)
        .join(Target, ExtractedInsight.target_id == Target.id)
        .join(ScrapedPost, ExtractedInsight.scraped_post_id == ScrapedPost.id)
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


@router.get("/local/{file_path:path}")
async def serve_local_pdf(file_path: str):
    """Serve a PDF directly from the local filesystem (dev only)."""
    from pathlib import Path
    from fastapi.responses import FileResponse
    pdf_path = Path(settings.reports_dir) / file_path
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
        result = _with_blob_token(lambda: vercel_blob.head(file_path))
    except Exception:
        result = None
    if not result:
        raise HTTPException(status_code=404, detail="File not found")

    url = result.get("downloadUrl") if not inline else result.get("url")
    url = url or result.get("url")
    if not url:
        raise HTTPException(status_code=404, detail="File not found")
    return RedirectResponse(url=url, status_code=302)
