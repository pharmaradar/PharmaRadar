"""Account tracking — its own feature, its own endpoints.

The CRUD half previously lived under /api/social/accounts, as a panel behind a
toggle on the Social page. That framing was wrong: tracking a named account is
not a setting of the keyword scan, it is the thing the client asked for. These
endpoints add what the panel could not answer — what has this account actually
published, when was it last checked, and refresh it now.
"""
import json
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


def _loads_obj(raw) -> dict:
    try:
        value = json.loads(raw) if raw else {}
        return value if isinstance(value, dict) else {}
    except Exception:                               # noqa: BLE001
        return {}


def _loads_list(raw) -> list:
    try:
        value = json.loads(raw) if raw else []
        return value if isinstance(value, list) else []
    except Exception:                               # noqa: BLE001
        return []


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
        "analysis": {
            "summary": account.analysis_summary,
            "so_what": account.analysis_so_what,
            "themes": _loads_list(account.analysis_themes),
            "generated_at": account.analysis_at.isoformat() if account.analysis_at else None,
            # True when posts arrived after the analysis was written, so the UI
            # can say "based on 12 of 22 posts" instead of implying it is current.
            "stale": (account.post_count or 0) > (account.analysis_post_count or 0),
            "post_count": account.analysis_post_count or 0,
            "sections": _loads_obj(account.analysis_sections),
        },
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
            "thumbnail_url": post.thumbnail_url,
            "platform": post.platform,
            "author": post.author,
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


@router.post("/{account_id}/analyse", dependencies=[Depends(require_admin)])
async def analyse_account(account_id: int, refresh: bool = False,
                          db: AsyncSession = Depends(get_db)):
    """What this account talks about, and what it means for us.

    Cached on the account: the same posts produce the same read, so paying for
    it on every open would be charging twice for one answer. `refresh=true`
    forces a rewrite, and the cache is also bypassed once new posts have landed.
    """
    account = await db.get(TrackedAccount, account_id)
    if not account:
        raise HTTPException(404, "account not found")

    fresh_needed = (
        refresh
        or not account.analysis_summary
        or (account.post_count or 0) > (account.analysis_post_count or 0)
    )
    if not fresh_needed:
        return {**_out(account), "cached": True}

    rows = await db.execute(
        select(SocialPost)
        .where(SocialPost.tracked_account_id == account_id)
        .where(social_not_ae())
        .order_by(desc(SocialPost.scraped_at))
        .limit(40)
    )
    posts = rows.scalars().all()
    if not posts:
        raise HTTPException(422, "No posts collected for this account yet")

    excerpts = "\n\n".join(
        f"[{i}] ({p.platform}, {p.likes or 0} likes, "
        f"{p.posted_at.date().isoformat() if p.posted_at else 'undated'}) "
        f"{(p.text or '')[:500]}"
        for i, p in enumerate(posts, 1) if (p.text or "").strip()
    )[:16000]

    # Voice distribution and volume are COMPUTED from the rows, not written by
    # the model — a chart that presents an invention as a measurement is worse
    # than no chart, because it gets acted on. The model interprets them.
    from app.services.market_report import compute_volume
    from app.services.voice_profile import build_breakdown

    # `is_tracked_kol` must reflect what this account actually is. Hardcoding it
    # true filed the Haute Autorité de Santé under "KOLs" at 100% — an
    # institution presented as a key opinion leader, which is exactly the kind
    # of confident-and-wrong figure a reader would act on.
    is_kol = (account.role or "").lower() == "kol"
    as_items = [{
        "author": p.author or account.handle,
        "url": p.post_url,
        "is_tracked_kol": is_kol,
        "target_type": account.role,
        "kind": f"{p.platform} post",
        "platform": p.platform,
        "engagement": (p.likes or 0) + (p.comments or 0) + (p.views or 0),
        "date": p.posted_at.date().isoformat() if p.posted_at else "",
    } for p in posts]
    voices = build_breakdown(as_items)
    volume = compute_volume(as_items, 365)
    voice_rows = voices.as_rows()

    prompt = (
        "You are a pharma intelligence analyst monitoring the French market for "
        "Roche. Below are recent posts from ONE tracked account. Write a "
        "market-research style analysis of this account.\n\n"
        "Output EXACTLY these sections, each on its own line starting with the "
        "marker, and nothing else:\n\n"
        "##EXEC_SUMMARY##\n2-3 paragraphs: who this account is, what they "
        "publish, their angle and who they speak to.\n\n"
        "##SO_WHAT##\n2-3 paragraphs on the strategic implication for a pharma "
        "medical affairs team — the signal, opportunity or risk, and what to do "
        "about it. Be specific and actionable.\n\n"
        "##WHAT_IS_SAID##\n2-3 paragraphs on the substance: the actual claims, "
        "topics and positions in these posts. Cite post numbers like [3].\n\n"
        "##VOICES##\n1-2 paragraphs interpreting WHO is speaking here, given "
        f"this account is classified as: {account.role or 'unclassified'}. "
        "State plainly that this is a single account, so the distribution "
        "describes one voice rather than a market.\n\n"
        "##VOLUME##\n1-2 paragraphs interpreting how much and how often this "
        f"account posts, given {len(posts)} posts analysed.\n\n"
        "##SUBTOPICS##\n4-6 lines starting '- ': the sub-topics worth tracking "
        "from this account, each with why it matters.\n\n"
        "Base every statement on the posts. Do not speculate beyond them. "
        "Write in English about French-language material.\n\n"
        f"Account: @{account.handle} ({account.platform})"
        f"{' — ' + account.label if account.label else ''}\n"
        f"Posts analysed: {len(posts)}\n\n{excerpts}"
    )

    from app.services.llm_router import call_llm_async
    try:
        # gemini-2.5-flash spends this budget on reasoning as well as output, so
        # a small cap truncates the tail sections silently — 1200 lost SO WHAT
        # and THEMES entirely.
        reply = await call_llm_async([{"role": "user", "content": prompt}], max_tokens=8192)
    except Exception as exc:                        # noqa: BLE001
        raise HTTPException(502, f"LLM call failed: {str(exc)[:200]}") from exc

    from app.services.synthesizer import extract_section, parse_bullets, trim_incomplete

    raw = reply or ""
    sections = {
        "exec_summary": trim_incomplete(extract_section(raw, "EXEC_SUMMARY")),
        "so_what": trim_incomplete(extract_section(raw, "SO_WHAT")),
        "what_is_said": trim_incomplete(extract_section(raw, "WHAT_IS_SAID")),
        "voices_note": trim_incomplete(extract_section(raw, "VOICES")),
        "volume_note": trim_incomplete(extract_section(raw, "VOLUME")),
        "subtopics": parse_bullets(extract_section(raw, "SUBTOPICS")),
        "voice_rows": voice_rows,
        "voice_exact_share": round(voices.exact_share * 100),
        "volume": volume,
        "item_count": len(posts),
    }

    # The card shows the headline fields, so they stay as their own columns.
    account.analysis_summary = sections["exec_summary"] or raw.strip()[:2000]
    account.analysis_so_what = sections["so_what"] or None
    account.analysis_themes = json.dumps(sections["subtopics"])
    account.analysis_sections = json.dumps(sections)
    account.analysis_at = datetime.now(timezone.utc)
    # Record what it was written from, so staleness is measurable rather than
    # guessed from a timestamp.
    account.analysis_post_count = account.post_count or len(posts)
    await db.commit()
    await db.refresh(account)
    return {**_out(account), "cached": False, "posts_analysed": len(posts)}


