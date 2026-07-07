"""LLM tasks — run on the 'llm' queue."""
import structlog

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.llm.extract_insights",
    queue="llm",
    max_retries=4,
    default_retry_delay=30,
    acks_late=True,
)
def extract_insights(self, post_id: int, run_id: int) -> dict:
    """Run LLM insight extraction on a single scraped post."""
    from app.services.extractor import ExtractorService
    from app.services.run_context import RunContext
    from app.tasks.utils import patch_run

    log = logger.bind(post_id=post_id, run_id=run_id, task_id=self.request.id)
    log.info("extract_insights.started")
    try:
        ctx = RunContext(run_id=run_id, task_id=self.request.id)
        result = ExtractorService().extract(post_id=post_id, ctx=ctx)
        saved = result.get("insights_saved", 0)
        log.info("extract_insights.done", insights=saved)
        patch_run(run_id, **{"+insights_extracted": saved, "+llm_calls_used": 1})
        return result
    except Exception as exc:
        log.warning("extract_insights.retry", exc=str(exc), retries=self.request.retries)
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))


@celery_app.task(
    bind=True,
    name="app.tasks.llm.extract_target_posts",
    queue="llm",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
    soft_time_limit=600,
    time_limit=700,
)
def extract_target_posts(self, target_id: int, run_id: int) -> dict:
    """Extract insights for all unprocessed posts of a target. Runs synchronously
    so generate_summary always sees completed insights."""
    import asyncio
    from app.services.extractor import ExtractorService
    from app.services.run_context import RunContext
    from app.tasks.utils import patch_run

    log = logger.bind(target_id=target_id, run_id=run_id, task_id=self.request.id)
    log.info("extract_target_posts.started")

    async def _get_unextracted_ids():
        from app.database import CelerySessionLocal
        from app.models import ScrapedPost, ExtractedInsight
        from sqlalchemy import select
        async with CelerySessionLocal() as sess:
            extracted_subq = select(ExtractedInsight.scraped_post_id).where(
                ExtractedInsight.target_id == target_id
            ).scalar_subquery()
            rows = await sess.execute(
                select(ScrapedPost.id)
                .where(ScrapedPost.target_id == target_id)
                .where(~ScrapedPost.id.in_(extracted_subq))
                .order_by(ScrapedPost.scraped_at.desc())
                .limit(25)
            )
            return rows.scalars().all()

    try:
        post_ids = asyncio.run(_get_unextracted_ids())
        if not post_ids:
            log.info("extract_target_posts.nothing_to_extract")
            return {"extracted": 0, "insights_saved": 0}

        ctx = RunContext(run_id=run_id, task_id=self.request.id)
        extractor = ExtractorService()
        total_insights = 0
        for post_id in post_ids:
            result = extractor.extract(post_id=post_id, ctx=ctx)
            saved = result.get("insights_saved", 0)
            total_insights += saved
            patch_run(run_id, **{"+insights_extracted": saved, "+llm_calls_used": 1})

        log.info("extract_target_posts.done", posts=len(post_ids), insights=total_insights)
        return {"extracted": len(post_ids), "insights_saved": total_insights}
    except Exception as exc:
        log.warning("extract_target_posts.retry", exc=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    bind=True,
    name="app.tasks.llm.generate_summary",
    queue="llm",
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def generate_summary(self, target_id: int, run_id: int) -> dict:
    """Generate PersonSummary (bullets + so-what) for a target after extraction."""
    from app.services.extractor import ExtractorService
    from app.services.run_context import RunContext

    log = logger.bind(target_id=target_id, run_id=run_id, task_id=self.request.id)
    log.info("generate_summary.started")
    from app.tasks.utils import patch_run
    try:
        ctx = RunContext(run_id=run_id, task_id=self.request.id)
        result = ExtractorService().summarise(target_id=target_id, run_id=run_id, ctx=ctx)
        log.info("generate_summary.done")
        patch_run(run_id, **{"+llm_calls_used": 1})
        return result
    except Exception as exc:
        log.warning("generate_summary.retry", exc=str(exc))
        raise self.retry(exc=exc)
