"""The account-tracking pipeline.

Deliberately separate from `tasks.social`. The social scan is keyword-driven:
it asks "who is talking about lung cancer this week" and pays Apify to guess.
This pipeline asks a different question — "what did THESE accounts publish" —
and the answer is not a guess, so it should not queue behind the keyword scan,
share its schedule, or fail when it fails.

Per-platform capture, measured 2026-08-12:

  X/Twitter   TinyFish `site:x.com/<handle>`                    free, exact
  LinkedIn    TinyFish `<handle> site:fr.linkedin.com/posts`    free, 9/10
  Instagram   Apify profile scraper                             billed
  Facebook    Apify posts scraper (startUrls)                   billed

X and LinkedIn cost nothing, so they run on every sweep. Instagram and Facebook
have no working free lane at all — Facebook account search returns zero results
and Instagram's prefix-matches a different account (`liguecontrelecancer.34`) —
so those two only run when Apify is configured.

Every lane writes `tracked_account_id`, and every account gets `last_scanned_at`
and `last_scan_status` stamped whether it succeeded or not. An account that
returns nothing is recorded as 'empty' rather than left looking untouched: that
is what a mistyped handle looks like, and it is the only way it becomes visible.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import structlog
from sqlalchemy import func, select, update

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

# Redis key holding sweep progress, so the UI can show a live bar without
# polling the database.
_STATUS_KEY = "account_scan:status"
_STATUS_TTL = 3600

# Posts fetched per account per sweep. The free lanes return a page of ten;
# asking for more costs another search without reaching further back.
_PER_ACCOUNT = 10
_FREE_PLATFORMS = ("twitter", "linkedin")
_PAID_PLATFORMS = ("instagram", "facebook")


def _set_status(**fields) -> None:
    """Best-effort progress publication. Never fails the scan."""
    try:
        import redis as _redis

        from app.config import get_settings
        client = _redis.Redis.from_url(get_settings().redis_url, socket_timeout=2)
        current = {}
        raw = client.get(_STATUS_KEY)
        if raw:
            current = json.loads(raw)
        current.update(fields)
        client.setex(_STATUS_KEY, _STATUS_TTL, json.dumps(current))
    except Exception as exc:                        # noqa: BLE001
        logger.debug("account_scan.status_failed", error=str(exc)[:120])


def read_status() -> dict:
    try:
        import redis as _redis

        from app.config import get_settings
        client = _redis.Redis.from_url(get_settings().redis_url, socket_timeout=2)
        raw = client.get(_STATUS_KEY)
        return json.loads(raw) if raw else {"running": False}
    except Exception:                               # noqa: BLE001
        return {"running": False}


async def _stamp(session, account_id: int, status: str) -> None:
    """Record the outcome of a scan against the account itself."""
    from app.models import SocialPost, TrackedAccount

    total = await session.execute(
        select(func.count()).select_from(SocialPost)
        .where(SocialPost.tracked_account_id == account_id)
    )
    await session.execute(
        update(TrackedAccount).where(TrackedAccount.id == account_id).values(
            last_scanned_at=datetime.now(timezone.utc),
            last_scan_status=status,
            post_count=total.scalar() or 0,
        )
    )
    await session.commit()


async def _link_posts(session, account_id: int, urls: list[str]) -> None:
    """Point the freshly-ingested rows at the account that produced them.

    The lanes reuse `tasks.social._ingest_posts`, which knows nothing about
    tracked accounts, so the FK is set here by URL rather than duplicating the
    whole insert path for one column.
    """
    from app.models import SocialPost

    if not urls:
        return
    await session.execute(
        update(SocialPost)
        .where(SocialPost.post_url.in_(urls),
               SocialPost.tracked_account_id.is_(None))
        .values(tracked_account_id=account_id)
    )
    await session.commit()


def _authored_by(posts: list[dict], handle: str) -> list[dict]:
    """Keep only the posts this account actually wrote.

    Posts with no author at all are dropped: unverifiable attribution is the
    thing being guarded against, so keeping them would defeat the check.
    """
    wanted = (handle or "").strip().lstrip("@").lower()
    if not wanted:
        return posts
    kept = []
    for post in posts or []:
        author = (post.get("author") or "").strip().lstrip("@").lower()
        if author and author == wanted:
            kept.append(post)
    return kept


async def _scan_one(account, lang_filter: str = "fr") -> dict:
    """Collect one account's recent posts. Returns a small result summary."""
    from app.database import CelerySessionLocal
    from app.services import apify_client
    from app.services.tinyfish_social import fetch_account_posts
    from app.tasks.social import _ingest_posts

    handle = (account.handle or "").strip().lstrip("@")
    platform = account.platform
    loop = asyncio.get_running_loop()
    posts: list[dict] = []

    try:
        if platform in ("twitter", "linkedin"):
            # One exact, account-pinned search, then an exact author check —
            # `fetch_account_posts` deliberately avoids the variant expansion
            # that would add an unpinned global search to the account's feed.
            posts = await loop.run_in_executor(
                None, lambda: fetch_account_posts(
                    platform, handle, max_results=_PER_ACCOUNT,
                    lang_filter=lang_filter))

        elif platform == "instagram":
            if not apify_client.is_configured():
                return {"status": "skipped", "saved": 0, "reason": "apify_not_configured"}
            posts = await loop.run_in_executor(
                None, lambda: apify_client.fetch_instagram_accounts(
                    [handle], max_per_account=_PER_ACCOUNT, window_days=90))
            # The profile scraper also returns posts the account was tagged in
            # or collaborated on. Measured: 2 of 24 came back authored by
            # someone else, and linking those to this account would credit it
            # with reach it never had. Twitter and LinkedIn already verify
            # authorship this way; Instagram gives the username outright.
            posts = _authored_by(posts, handle)

        elif platform == "facebook":
            if not apify_client.is_configured():
                return {"status": "skipped", "saved": 0, "reason": "apify_not_configured"}
            page_url = (account.url or "").strip() or f"https://www.facebook.com/{handle}"
            posts = await loop.run_in_executor(
                None, lambda: apify_client.fetch_platform(
                    "facebook", "", max_results=_PER_ACCOUNT,
                    window_days=90, page_urls=[page_url]))
        else:
            return {"status": "error", "saved": 0, "reason": f"unknown platform {platform}"}

    except Exception as exc:                        # noqa: BLE001 - one account must not end the sweep
        logger.warning("account_scan.failed", platform=platform, handle=handle,
                       error=str(exc)[:160])
        async with CelerySessionLocal() as session:
            await _stamp(session, account.id, "error")
        return {"status": "error", "saved": 0, "reason": str(exc)[:160]}

    posts = posts or []
    saved = 0
    if posts:
        async with CelerySessionLocal() as session:
            saved = await _ingest_posts(
                session, posts, kind="account", topic=f"account:{handle}",
                query=f"tracked:{platform}:{handle.lower()}", tracked=(handle,))
            await _link_posts(session, account.id,
                              [p.get("post_url") for p in posts if p.get("post_url")])

    # 'empty' is a real outcome, not a failure: the handle may simply be wrong.
    status = "ok" if posts else "empty"
    async with CelerySessionLocal() as session:
        await _stamp(session, account.id, status)
    logger.info("account_scan.one", platform=platform, handle=handle,
                returned=len(posts), saved=saved, status=status)
    return {"status": status, "saved": saved, "returned": len(posts)}


async def _run_sweep(account_ids: list[int] | None = None,
                     publish_status: bool = True) -> dict:
    from app.database import CelerySessionLocal
    from app.models import AppSettings, TrackedAccount
    from app.services import apify_client

    async with CelerySessionLocal() as session:
        query = select(TrackedAccount).where(TrackedAccount.active.is_(True))
        if account_ids:
            query = query.where(TrackedAccount.id.in_(account_ids))
        accounts = (await session.execute(query.order_by(TrackedAccount.id))).scalars().all()
        settings = await session.get(AppSettings, 1)
        lang_filter = getattr(settings, "social_lang_filter", "fr") or "fr"

    # The paid platforms have no free fallback, so skip them rather than spend a
    # sweep discovering that every Instagram account returns nothing.
    if not apify_client.is_configured():
        skipped = [a for a in accounts if a.platform in _PAID_PLATFORMS]
        accounts = [a for a in accounts if a.platform in _FREE_PLATFORMS]
        if skipped:
            logger.info("account_scan.apify_off", skipped=len(skipped))

    total = len(accounts)
    # One shared status key describes "the sweep". Individual refreshes must not
    # write to it: queueing three accounts during a full sweep would have each
    # overwrite total/done with 1/1, so the sweep's progress bar would report
    # nonsense. The UI tracks single refreshes by their scan timestamp instead.
    def _publish(**fields):
        if publish_status:
            _set_status(**fields)

    _publish(running=True, total=total, done=0, saved=0,
             started_at=datetime.now(timezone.utc).isoformat(), error=None)
    if not total:
        _publish(running=False, finished_at=datetime.now(timezone.utc).isoformat())
        return {"accounts": 0, "saved": 0}

    done = saved_total = 0
    for account in accounts:
        result = await _scan_one(account, lang_filter)
        done += 1
        saved_total += result.get("saved", 0)
        _publish(done=done, saved=saved_total,
                 current=f"{account.platform}:{account.handle}")

    _publish(running=False, done=done, saved=saved_total,
             finished_at=datetime.now(timezone.utc).isoformat())
    logger.info("account_scan.done", accounts=done, saved=saved_total)
    return {"accounts": done, "saved": saved_total}


@celery_app.task(
    bind=True,
    name="app.tasks.accounts.account_scan",
    queue="scrape",
    # Not acks_late: a re-delivered sweep would re-search every account for no
    # gain, and the dedup hash means a lost sweep costs only freshness.
    acks_late=False,
    soft_time_limit=1800,
    time_limit=2100,
)
def account_scan(self, account_ids: list[int] | None = None) -> dict:
    """Sweep every active tracked account, or just the ids given."""
    return asyncio.run(_run_sweep(account_ids))


@celery_app.task(
    bind=True,
    name="app.tasks.accounts.refresh_account",
    queue="scrape",
    acks_late=False,
    soft_time_limit=300,
    time_limit=420,
)
def refresh_account(self, account_id: int) -> dict:
    """On-demand refresh of a single account, behind the UI's Refresh button.

    Several of these can be in flight at once — the UI queues them — so this
    deliberately does not publish to the shared sweep status.
    """
    return asyncio.run(_run_sweep([account_id], publish_status=False))
