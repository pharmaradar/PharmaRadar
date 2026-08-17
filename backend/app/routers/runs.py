"""Run management: trigger, stop, poll progress, history."""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, desc, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import RunLog, RunStatus, Target
from app.auth import require_admin, require_admin_or_internal, require_superadmin
from app.config import get_settings
settings = get_settings()

router = APIRouter(prefix="/api/runs", tags=["runs"])


class TriggerRequest(BaseModel):
    limit: int | None = None


class RunOut(BaseModel):
    id: int
    status: str
    started_at: str
    completed_at: str | None
    total_targets: int
    targets_processed: int
    new_posts_found: int
    insights_extracted: int
    pdfs_generated: int
    current_target: str | None
    error_message: str | None
    llm_calls_used: int

    model_config = {"from_attributes": True}


def _run_to_out(r: RunLog) -> RunOut:
    return RunOut(
        id=r.id,
        status=r.status if isinstance(r.status, str) else r.status.value,
        started_at=r.started_at.isoformat(),
        completed_at=r.completed_at.isoformat() if r.completed_at else None,
        total_targets=r.total_targets,
        targets_processed=r.targets_processed,
        new_posts_found=r.new_posts_found,
        insights_extracted=r.insights_extracted,
        pdfs_generated=r.pdfs_generated,
        current_target=r.current_target,
        error_message=r.error_message,
        llm_calls_used=r.llm_calls_used,
    )


# require_admin_or_internal: the beat scheduler calls this endpoint with the
# internal token — a plain require_admin made every scheduled run 401 (latent
# until beat was actually deployed).
@router.post("/trigger", dependencies=[Depends(require_admin_or_internal)])
async def trigger_run(body: TriggerRequest, db: AsyncSession = Depends(get_db)):
    from celery import chain, chord, group
    from app.tasks.scrape import scrape_target, wave2_rescue
    from app.tasks.llm import generate_summary, extract_target_posts
    from app.tasks.pdf import generate_target_pdf, generate_run_summary_pdf

    # Reject if a run is already in progress
    existing = await db.execute(
        select(RunLog).where(RunLog.status == RunStatus.running).limit(1)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="A run is already in progress")

    idempotency_key = str(uuid.uuid4())

    rows = await db.execute(
        select(Target).where(Target.active == True).order_by(Target.name)
    )
    targets = rows.scalars().all()
    if body.limit:
        targets = targets[: body.limit]

    if not targets:
        raise HTTPException(status_code=422, detail="No active targets configured")

    run = RunLog(
        idempotency_key=idempotency_key,
        status=RunStatus.running,
        total_targets=len(targets),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    # ── Two-wave pipeline ────────────────────────────────────────────────────
    #
    # Wave 1 (all targets in parallel, fast):
    #   scrape_target  — free fetch only, NO agent calls
    #   → got posts?   — chain summary + pdf immediately
    #   → 0 posts?     — registers target in Redis for Wave 2, skips summary/pdf for now
    #
    # Wave 2 (chord callback, after ALL Wave 1 tasks complete):
    #   wave2_rescue   — agent on 0-post targets (known_urls + bot-blocked)
    #   → then summary + pdf for any rescued targets
    #   → then daily summary pdf + marks run success
    #
    # This ensures 0-post targets NEVER block other targets' scraping.

    # Wave 1: per target → scrape (fast) → summary → pdf for targets WITH posts
    # (wave2_rescue handles the 0-post ones via Redis)
    wave1_tasks = []
    for t in targets:
        per_target = chain(
            scrape_target.si(t.id, run.id, idempotency_key),
            extract_target_posts.si(t.id, run.id),
            generate_summary.si(t.id, run.id),
            generate_target_pdf.si(t.id, run.id),
        )
        wave1_tasks.append(per_target)

    # Wave 2 callback: rescue 0-post targets → then daily summary
    wave2_callback = chain(
        wave2_rescue.si(run.id),
        generate_run_summary_pdf.si(run.id),
    )

    pipeline = chord(group(*wave1_tasks), wave2_callback)
    async_result = pipeline.apply_async()

    run.celery_task_id = async_result.id
    await db.commit()

    # Set Redis flag so TinyFish key allocation knows pipeline is active
    try:
        import redis as _redis
        from app.services.scraper import PIPELINE_RUNNING_REDIS_KEY
        r = _redis.Redis.from_url(settings.redis_url, socket_timeout=2)
        r.set(PIPELINE_RUNNING_REDIS_KEY, "1", ex=7200)  # 2h TTL
    except Exception:
        pass

    return {"run_id": run.id, "idempotency_key": idempotency_key, "targets": len(targets)}


@router.get("/current")
async def current_run(db: AsyncSession = Depends(get_db)):
    row = await db.execute(
        select(RunLog).where(RunLog.status == RunStatus.running)
        .order_by(RunLog.started_at.desc()).limit(1)
    )
    run = row.scalar_one_or_none()
    if not run:
        return {"running": False}
    return {"running": True, **_run_to_out(run).model_dump()}


@router.post("/stop", dependencies=[Depends(require_admin)])
async def stop_run(db: AsyncSession = Depends(get_db)):
    # Cancel ALL currently-running rows in one go: stale rows can pile up
    # if a previous chord finished without a completion callback.
    from datetime import datetime, timezone
    rows = await db.execute(select(RunLog).where(RunLog.status == RunStatus.running))
    runs = list(rows.scalars().all())
    if not runs:
        raise HTTPException(status_code=404, detail="No active run")

    # Flip status first so the UI sees "stopped" on the very next poll,
    # even if Celery revoke is slow or unreachable.
    now = datetime.now(timezone.utc)
    task_ids: list[str] = []
    for r in runs:
        r.status = RunStatus.cancelled
        r.completed_at = now
        r.current_target = None
        if r.celery_task_id:
            task_ids.append(r.celery_task_id)
    await db.commit()

    # Best-effort task cancellation — don't fail the request if the broker is down.
    if task_ids:
        try:
            from app.tasks.celery_app import celery_app
            celery_app.control.revoke(task_ids, terminate=True, signal="SIGTERM")
        except Exception as exc:
            # Log but don't propagate — UI already reflects stopped state.
            import structlog
            structlog.get_logger(__name__).warning("stop_run.revoke_failed", exc=str(exc))

    # Clear pipeline Redis flag
    try:
        import redis as _redis
        from app.services.scraper import PIPELINE_RUNNING_REDIS_KEY
        _redis.Redis.from_url(settings.redis_url, socket_timeout=2).delete(PIPELINE_RUNNING_REDIS_KEY)
    except Exception:
        pass

    return {"stopped": True, "cancelled": [r.id for r in runs]}


# Superadmin-only, matching the frontend: Run History is behind SuperadminRoute
# in App.tsx and its nav entry is superadmin-flagged. Hiding the page alone left
# the data readable by any authenticated user straight from the API. Run history
# exposes operational internals (error messages, credit spend, target counts)
# that the client's own users have no reason to read.
# `/current` deliberately stays open to every signed-in user — the Dashboard and
# Settings pipeline bar both poll it.
@router.get("/", response_model=list[RunOut],
            dependencies=[Depends(require_superadmin)])
async def list_runs(db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(RunLog).order_by(desc(RunLog.started_at)).limit(50))
    return [_run_to_out(r) for r in rows.scalars().all()]


@router.post("/generate-pdfs", dependencies=[Depends(require_admin)])
async def generate_pdfs(db: AsyncSession = Depends(get_db)):
    """Regenerate PDFs for all active targets from existing insights — no scraping needed."""
    from celery import group, chain
    from app.tasks.pdf import generate_target_pdf, generate_run_summary_pdf
    from app.models import RunLog, RunStatus

    # Same guard as /trigger — a double-click here used to stack two concurrent
    # chord runs and leave an extra 'running' RunLog blocking the UI.
    existing = await db.execute(
        select(RunLog).where(RunLog.status == RunStatus.running).limit(1)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="A run is already in progress")

    rows = await db.execute(select(Target).where(Target.active == True).order_by(Target.name))
    targets = rows.scalars().all()
    if not targets:
        raise HTTPException(status_code=422, detail="No active targets configured")

    run = RunLog(
        status=RunStatus.running,
        total_targets=len(targets),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    from celery import chord
    pdf_tasks = [generate_target_pdf.si(t.id, run.id) for t in targets]
    pipeline = chord(group(*pdf_tasks), generate_run_summary_pdf.si(run.id))
    pipeline.apply_async()

    return {"status": "started", "run_id": run.id}


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: int, db: AsyncSession = Depends(get_db)):
    run = await db.get(RunLog, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_to_out(run)


@router.delete("/{run_id}", dependencies=[Depends(require_superadmin)])
async def delete_run(run_id: int, db: AsyncSession = Depends(get_db)):
    """Erase one run from history. SUPER ADMIN ONLY — it destroys audit trail.

    Safe by construction: `person_summaries.run_id` is the ONLY foreign key into
    run_logs, it's nullable, and it has no cascade. We NULL it rather than delete,
    because those rows hold the summary bullets the reports render and they
    outlive the run that produced them (a later generate-pdfs run reuses them).
    `scraped_posts` and `extracted_insights` carry no run FK at all, so insights
    are never touched — verified 2026-08-08 against the models.
    """
    run = await db.get(RunLog, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status == RunStatus.running:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a run that is still running — stop it first",
        )

    from app.models import PersonSummary
    res = await db.execute(
        update(PersonSummary).where(PersonSummary.run_id == run_id).values(run_id=None)
    )
    await db.delete(run)
    await db.commit()
    return {"deleted": run_id, "summaries_detached": res.rowcount or 0}


@router.post("/reset-all", dependencies=[Depends(require_admin)])
async def reset_all(db: AsyncSession = Depends(get_db)):
    """Delete all operational data (posts, insights, summaries, runs, reports, blobs)
    — keeps targets, burning topics/congresses (config) and settings."""
    import asyncio
    from app.models import (AgentMessage, BurningTopicReport, ExtractedInsight,
                            PersonSummary, ScrapedPost)
    from app.models.discovery_result import DiscoveryResult
    from app.models.social_post import SocialPost
    from app.config import get_settings

    # 1. DB: delete in FK-safe order — all operational + discovery + chat + social.
    # BurningTopicReport rows are generated output (topics/congresses themselves
    # are configuration, like targets — those stay).
    await db.execute(delete(ExtractedInsight))
    await db.execute(delete(PersonSummary))
    await db.execute(delete(ScrapedPost))
    await db.execute(delete(RunLog))
    await db.execute(delete(AgentMessage))
    await db.execute(delete(DiscoveryResult))
    await db.execute(delete(SocialPost))
    await db.execute(delete(BurningTopicReport))
    await db.commit()

    # 2. Vercel Blob: delete every report family (burning-topic/congress and
    # global-synthesis PDFs used to survive a destroy). Runs in the thread pool
    # via run_blob_op — thread-safe token handling, event loop stays free.
    blob_deleted = 0
    settings = get_settings()
    if settings.vercel_blob_token:
        import vercel_blob
        from app.services.vercel_blob_storage import run_blob_op

        def _delete_prefix(prefix: str) -> int:
            deleted = 0
            cursor = None
            while True:
                opts: dict = {"prefix": prefix, "limit": 1000}
                if cursor:
                    opts["cursor"] = cursor
                result = vercel_blob.list(opts)
                blobs = result.get("blobs", []) if isinstance(result, dict) else []
                for b in blobs:
                    vercel_blob.delete(b["url"])
                    deleted += 1
                if not result.get("hasMore"):
                    return deleted
                cursor = result.get("cursor")

        loop = asyncio.get_event_loop()
        for prefix in ("reports/", "burning-topics/", "global-synthesis/"):
            try:
                blob_deleted += await loop.run_in_executor(
                    None, lambda p=prefix: run_blob_op(lambda: _delete_prefix(p),
                                                       settings.vercel_blob_token))
            except Exception:
                pass  # non-fatal — DB is already clean

    # 3. Redis: flush ALL databases (DB0=app, DB1=Celery broker, DB2=Celery results)
    redis_reset = False
    try:
        import redis as _redis
        # Parse base URL (strip /db suffix) and flush all DBs
        base_url = settings.redis_url.rsplit("/", 1)[0]
        for db_num in range(3):  # DB0, DB1, DB2
            try:
                r = _redis.Redis.from_url(f"{base_url}/{db_num}", socket_timeout=3)
                r.flushdb()
            except Exception:
                pass
        redis_reset = True
    except Exception:
        pass

    return {"db_cleared": True, "blobs_deleted": blob_deleted, "redis_reset": redis_reset}
