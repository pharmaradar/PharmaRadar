"""PDF generation tasks — run on the 'pdf' queue."""
import structlog

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.pdf.generate_target_pdf",
    queue="pdf",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def generate_target_pdf(self, target_id: int, run_id: int) -> dict:
    """Generate per-target PDF report and validate the output file."""
    from app.services.pdf_generator import PDFService
    from app.services.run_context import RunContext
    from app.tasks.utils import patch_run

    log = logger.bind(target_id=target_id, run_id=run_id, task_id=self.request.id)
    log.info("generate_target_pdf.started")
    try:
        ctx = RunContext(run_id=run_id, task_id=self.request.id)
        result = PDFService().generate_target_report(target_id=target_id, run_id=run_id, ctx=ctx)
        log.info("generate_target_pdf.done", path=result.get("path"))
        patch_run(run_id, **{"+pdfs_generated": 1})
        return result
    except Exception as exc:
        log.warning("generate_target_pdf.retry", exc=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    bind=True,
    name="app.tasks.pdf.generate_daily_summary_pdf",
    queue="pdf",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def generate_daily_summary_pdf(self, run_id: int) -> dict:
    """Generate the combined daily summary PDF after all target PDFs are ready.

    This is the chord callback — it also flips the RunLog status from
    `running` to `success` (or `error` on a final failure) so the dashboard
    indicator clears when the pipeline truly finishes.
    """
    from app.services.pdf_generator import PDFService
    from app.services.run_context import RunContext

    log = logger.bind(run_id=run_id, task_id=self.request.id)
    log.info("generate_daily_summary_pdf.started")
    try:
        ctx = RunContext(run_id=run_id, task_id=self.request.id)
        result = PDFService().generate_daily_summary(run_id=run_id, ctx=ctx)
        log.info("generate_daily_summary_pdf.done", path=result.get("path"))
        _mark_run_finished(run_id, status="success")
        return result
    except Exception as exc:
        log.warning("generate_daily_summary_pdf.retry", exc=str(exc))
        try:
            raise self.retry(exc=exc)
        except Exception:
            # Out of retries — mark the run as errored so the UI unblocks.
            _mark_run_finished(run_id, status="error", error_message=str(exc)[:500])
            raise


def _mark_run_finished(run_id: int, status: str, error_message: str | None = None) -> None:
    """Sync helper to flip RunLog.status from running → success/error.

    Skips the update if the row is already in a terminal state (e.g. user cancelled).
    """
    import asyncio
    from datetime import datetime, timezone

    async def _update():
        from app.database import CelerySessionLocal
        from app.models import RunLog, RunStatus
        async with CelerySessionLocal() as sess:
            run = await sess.get(RunLog, run_id)
            if not run or run.status != RunStatus.running:
                return
            run.status = status
            run.completed_at = datetime.now(timezone.utc)
            run.current_target = None   # ← clear ONLY when the run truly ends
            if error_message:
                run.error_message = error_message
            await sess.commit()

    try:
        asyncio.run(_update())
    except Exception as exc:
        logger.warning("mark_run_finished.failed", run_id=run_id, exc=str(exc))
