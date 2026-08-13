"""Account tracking — its own feature, its own endpoints.

The CRUD half previously lived under /api/social/accounts, as a panel behind a
toggle on the Social page. That framing was wrong: tracking a named account is
not a setting of the keyword scan, it is the thing the client asked for. These
endpoints add what the panel could not answer — what has this account actually
published, when was it last checked, and refresh it now.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, require_admin
from app.database import get_db
from app.models import SocialPost, TrackedAccount
from app.models.tracked_account import PLATFORMS
from app.services.ae_filter import social_not_ae

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

# Roles are advisory groupings for the UI, not a constraint on what can be
# tracked — the client knows their market better than a list written here.
ROLES = ("kol", "institution", "pharma", "patient_association", "media", "other")


class AccountIn(BaseModel):
    platform: str
    handle: str
    url: str | None = None
    label: str | None = None
    full_name: str | None = None
    role: str | None = None
    category: str | None = None
    notes: str | None = None


class AccountPatch(BaseModel):
    handle: str | None = None
    url: str | None = None
    label: str | None = None
    full_name: str | None = None
    role: str | None = None
    category: str | None = None
    notes: str | None = None
    active: bool | None = None


def _normalise_handle(platform: str, raw: str) -> str:
    """Reduce whatever the user pasted to the handle the scrapers expect.

    People paste profile URLs as often as they type handles, and storing both
    forms for one account defeats the UNIQUE (platform, handle) constraint —
    the same account would be tracked twice and counted twice.
    """
    value = (raw or "").strip()
    if not value:
        return ""
    if "://" in value or value.startswith("www."):
        path = value.split("://")[-1]
        parts = [p for p in path.split("/")[1:] if p]
        # linkedin.com/company/<slug> and /in/<slug> carry the slug one deeper.
        if parts and parts[0] in ("company", "in", "posts"):
            parts = parts[1:]
        value = parts[0] if parts else value
    value = value.split("?")[0].split("#")[0]
    return value.strip().lstrip("@").strip("/")


def _out(account: TrackedAccount) -> dict:
    return {
        "id": account.id,
        "platform": account.platform,
        "handle": account.handle,
        "url": account.url,
        "label": account.label,
        "full_name": account.full_name,
        "role": account.role,
        "category": account.category,
        "notes": account.notes,
        "active": account.active,
        "post_count": account.post_count or 0,
        "last_scanned_at": account.last_scanned_at.isoformat() if account.last_scanned_at else None,
        "last_scan_status": account.last_scan_status,
    }


@router.get("")
async def list_accounts(db: AsyncSession = Depends(get_db),
                        user=Depends(get_current_user)):
    rows = await db.execute(
        select(TrackedAccount).order_by(TrackedAccount.platform, TrackedAccount.handle)
    )
    accounts = [_out(a) for a in rows.scalars().all()]
    return {
        "accounts": accounts,
        "platforms": list(PLATFORMS),
        "roles": list(ROLES),
        "totals": {
            "accounts": len(accounts),
            "active": sum(1 for a in accounts if a["active"]),
            "producing": sum(1 for a in accounts if a["post_count"] > 0),
            "posts": sum(a["post_count"] for a in accounts),
        },
    }


@router.post("", status_code=201, dependencies=[Depends(require_admin)])
async def create_account(body: AccountIn, db: AsyncSession = Depends(get_db)):
    platform = (body.platform or "").strip().lower()
    if platform not in PLATFORMS:
        raise HTTPException(422, f"platform must be one of {', '.join(PLATFORMS)}")
    handle = _normalise_handle(platform, body.handle)
    if not handle:
        raise HTTPException(422, "handle is required")

    existing = await db.execute(
        select(TrackedAccount).where(TrackedAccount.platform == platform,
                                     func.lower(TrackedAccount.handle) == handle.lower())
    )
    if existing.scalars().first():
        raise HTTPException(409, f"{handle} is already tracked on {platform}")

    account = TrackedAccount(
        platform=platform, handle=handle,
        url=(body.url or "").strip() or None,
        label=(body.label or "").strip() or None,
        full_name=(body.full_name or "").strip() or None,
        role=(body.role or "").strip() or None,
        category=(body.category or "").strip() or None,
        notes=(body.notes or "").strip() or None,
        active=True,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)

    # Collect it immediately. A newly added account that shows nothing until the
    # next nightly sweep looks broken, and this is also the fastest way to find
    # out the handle was mistyped.
    try:
        from app.tasks.accounts import refresh_account
        refresh_account.delay(account.id)
    except Exception:                               # noqa: BLE001 - queue down must not fail the create
        pass
    return _out(account)


@router.patch("/{account_id}", dependencies=[Depends(require_admin)])
async def update_account(account_id: int, body: AccountPatch,
                         db: AsyncSession = Depends(get_db)):
    account = await db.get(TrackedAccount, account_id)
    if not account:
        raise HTTPException(404, "account not found")

    data = body.model_dump(exclude_unset=True)
    if "handle" in data and data["handle"]:
        data["handle"] = _normalise_handle(account.platform, data["handle"])
    for field, value in data.items():
        setattr(account, field, value)
    await db.commit()
    await db.refresh(account)
    return _out(account)


@router.delete("/{account_id}", dependencies=[Depends(require_admin)])
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    account = await db.get(TrackedAccount, account_id)
    if not account:
        raise HTTPException(404, "account not found")
    await db.delete(account)
    await db.commit()
    # Posts survive: the FK is ON DELETE SET NULL. Removing an account stops
    # tracking it, it does not erase what it already said.
    return {"deleted": account_id}


@router.get("/status")
async def scan_status(user=Depends(get_current_user)):
    """Progress of the current sweep, for the page's progress bar."""
    from app.tasks.accounts import read_status
    return read_status()


@router.post("/scan", dependencies=[Depends(require_admin)])
async def trigger_scan():
    """Sweep every active account now."""
    from app.tasks.accounts import account_scan
    try:
        task = account_scan.delay()
    except Exception as exc:                        # noqa: BLE001
        raise HTTPException(503, f"queue unavailable: {str(exc)[:120]}") from exc
    return {"queued": True, "task_id": task.id}


@router.post("/{account_id}/refresh", dependencies=[Depends(require_admin)])
async def refresh_one(account_id: int, db: AsyncSession = Depends(get_db)):
    """Collect one account right now — the 'Refresh' button on its row."""
    account = await db.get(TrackedAccount, account_id)
    if not account:
        raise HTTPException(404, "account not found")
    from app.tasks.accounts import refresh_account
    try:
        task = refresh_account.delay(account_id)
    except Exception as exc:                        # noqa: BLE001
        raise HTTPException(503, f"queue unavailable: {str(exc)[:120]}") from exc
    return {"queued": True, "task_id": task.id, "account_id": account_id}


@router.get("/{account_id}")
async def account_detail(account_id: int, days: int = 90, limit: int = 50,
                         db: AsyncSession = Depends(get_db),
                         user=Depends(get_current_user)):
    """One account: its posts, its engagement, and how active it has been."""
    account = await db.get(TrackedAccount, account_id)
    if not account:
        raise HTTPException(404, "account not found")

    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = await db.execute(
        select(SocialPost)
        .where(SocialPost.tracked_account_id == account_id,
               SocialPost.scraped_at >= since)
        .where(social_not_ae())
        .order_by(desc(SocialPost.scraped_at))
        .limit(limit)
    )
    posts = rows.scalars().all()

    def _post_out(post: SocialPost) -> dict:
        return {
            "id": post.id,
            "url": post.post_url,
            "text": post.text,
            "likes": post.likes or 0,
            "comments": post.comments or 0,
            "views": post.views or 0,
            "language": post.language,
            "kind": post.kind,
            # 'posted' is when they published, 'collected' is when we looked.
            # LinkedIn and X search results carry no publication date, so the
            # UI must not present the second as the first.
            "posted_at": post.posted_at.isoformat() if post.posted_at else None,
            "collected_at": post.scraped_at.isoformat() if post.scraped_at else None,
        }

    engagement = sum((p.likes or 0) + (p.comments or 0) + (p.views or 0) for p in posts)
    return {
        "account": _out(account),
        "window_days": days,
        "posts": [_post_out(p) for p in posts],
        "stats": {
            "posts_in_window": len(posts),
            "total_engagement": engagement,
            "dated_posts": sum(1 for p in posts if p.posted_at),
        },
    }
