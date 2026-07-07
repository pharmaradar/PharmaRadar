"""Scrape tasks — run on the 'scrape' queue.

Two-wave pipeline:
  Wave 1  scrape_target   — free fetch only (fast, no agent), all targets in parallel
  Wave 2  wave2_rescue    — agent on 0-post targets after Wave 1 completes
"""
import structlog

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

# Redis key pattern: wave2:{run_id}  →  JSON list of {target_id, bot_blocked, idempotency_key}
_WAVE2_KEY = "wave2:{run_id}"


@celery_app.task(
    bind=True,
    name="app.tasks.scrape.scrape_target",
    queue="scrape",
    max_retries=2,
    default_retry_delay=60,
    acks_late=True,
)
def scrape_target(self, target_id: int, run_id: int, idempotency_key: str) -> dict:
    """Wave 1 — free fetch only. Fast.
    If 0 posts found, registers this target for Wave 2 agent rescue via Redis."""
    from app.services.scraper import ScrapeService
    from app.services.run_context import RunContext
    from app.tasks.utils import patch_run

    log = logger.bind(target_id=target_id, run_id=run_id, task_id=self.request.id)
    log.info("scrape_target.started")

    # Show current target in dashboard
    try:
        import asyncio
        async def _get_name():
            from app.database import CelerySessionLocal
            from app.models import Target
            async with CelerySessionLocal() as sess:
                t = await sess.get(Target, target_id)
                return t.name if t else str(target_id)
        target_name = asyncio.run(_get_name())
    except Exception:
        target_name = str(target_id)

    patch_run(run_id, current_target=target_name)

    try:
        ctx = RunContext(run_id=run_id, task_id=self.request.id)
        result = ScrapeService().scrape(
            target_id=target_id, ctx=ctx, idempotency_key=idempotency_key
        )
        new_posts = result.get("new_posts", 0)
        log.info("scrape_target.done", new_posts=new_posts,
                 needs_rescue=result.get("needs_rescue", False))

        # NOTE: don't clear current_target=None here.
        # All targets run in parallel under the chord+group; clearing it would race
        # with other still-running workers and make the dashboard show "initialising..."
        # while targets are actively being scraped. The final clear happens in
        # _mark_run_finished (generate_daily_summary_pdf) when the run truly ends.
        patch_run(run_id, **{"+new_posts_found": new_posts, "+targets_processed": 1})

        # Register for Wave 2 if no posts found in Wave 1
        if result.get("needs_rescue"):
            _register_for_wave2(run_id, target_id, result.get("bot_blocked", []), idempotency_key)

        return result

    except Exception as exc:
        log.warning("scrape_target.retry", exc=str(exc))
        raise self.retry(exc=exc)


def _register_for_wave2(run_id: int, target_id: int,
                         bot_blocked: list, idempotency_key: str) -> None:
    """Add this target to the Redis Wave 2 queue (called after 0-post Wave 1)."""
    import json
    try:
        import redis as _redis
        from app.config import get_settings
        r = _redis.Redis.from_url(get_settings().redis_url, socket_timeout=2)
        key = _WAVE2_KEY.format(run_id=run_id)
        entry = json.dumps({"target_id": target_id, "bot_blocked": bot_blocked,
                            "idempotency_key": idempotency_key})
        r.rpush(key, entry)
        r.expire(key, 86400)
        logger.info("wave2.registered", target_id=target_id, run_id=run_id)
    except Exception as exc:
        logger.warning("wave2.register_failed", exc=str(exc))


@celery_app.task(
    bind=True,
    name="app.tasks.scrape.wave2_rescue",
    queue="scrape",
    max_retries=1,
    default_retry_delay=30,
    acks_late=True,
    soft_time_limit=1800,
    time_limit=20000,
)
def wave2_rescue(self, run_id: int) -> dict:
    """Wave 2 — agent rescue for all targets that got 0 posts in Wave 1.
    Runs after the Wave 1 chord completes. Fires summary + pdf for rescued targets."""
    import json
    log = logger.bind(run_id=run_id, task_id=self.request.id)

    # Read Wave 2 targets from Redis
    targets_to_rescue = []
    try:
        import redis as _redis
        from app.config import get_settings
        r = _redis.Redis.from_url(get_settings().redis_url, socket_timeout=2)
        key = _WAVE2_KEY.format(run_id=run_id)
        raw_list = r.lrange(key, 0, -1)
        r.delete(key)
        targets_to_rescue = [json.loads(x) for x in raw_list]
    except Exception as exc:
        log.warning("wave2.redis_read_failed", exc=str(exc))

    log.info("wave2_rescue.start", targets=len(targets_to_rescue))

    if not targets_to_rescue:
        log.info("wave2_rescue.nothing_to_rescue")
        return {"rescued": 0}

    from app.services.scraper import ScrapeService
    from app.services.run_context import RunContext
    from app.tasks.utils import patch_run
    from app.tasks.llm import generate_summary, extract_target_posts
    from app.tasks.pdf import generate_target_pdf

    total_rescued = 0

    for entry in targets_to_rescue:
        target_id  = entry["target_id"]
        bot_blocked = entry.get("bot_blocked", [])
        ikey       = entry.get("idempotency_key", f"rescue_{run_id}")

        ctx = RunContext(run_id=run_id, task_id=self.request.id)
        result = ScrapeService().rescue(
            target_id=target_id, ctx=ctx,
            idempotency_key=ikey, bot_blocked_urls=bot_blocked,
        )
        rescued = result.get("rescue_posts", 0)
        total_rescued += rescued

        if rescued > 0:
            patch_run(run_id, **{"+new_posts_found": rescued})
            log.info("wave2.rescued", target_id=target_id, posts=rescued)

        # Chain summary → pdf and wait (max 3 min). If it takes longer, skip and
        # move on so the daily summary isn't blocked indefinitely.
        try:
            from celery import chain as _chain
            from celery.exceptions import TimeoutError as CeleryTimeout
            result = _chain(
                extract_target_posts.si(target_id, run_id),
                generate_summary.si(target_id, run_id),
                generate_target_pdf.si(target_id, run_id),
            ).apply_async()
            result.get(timeout=180, propagate=False)  # 3-min hard cap
        except CeleryTimeout:
            log.warning("wave2.summary_pdf_timeout", target_id=target_id, timeout=180)
        except Exception as exc:
            log.warning("wave2.summary_pdf_failed", target_id=target_id, exc=str(exc))

    log.info("wave2_rescue.done", total_rescued=total_rescued)
    return {"rescued": total_rescued}
