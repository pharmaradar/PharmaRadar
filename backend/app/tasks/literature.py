"""Pull KOL publications and competitor trials into the existing pipeline.

These land as ScrapedPost rows against their target, which means every module
built on that table — the KOL dashboard, insight extraction, syntheses, reports,
PDFs — picks them up with no further work. That is the whole reason for storing
them here rather than in a table of their own.

Why this lane exists at all: measured before it did, the KOL corpus was 27
documents, while six tracked KOLs had 461 papers in Europe PMC and the four
tracked companies had 450 lung-cancer trials near Paris. Both APIs are free and
official, so this is the cheapest content in the platform by a wide margin.
"""
from __future__ import annotations

import asyncio

import structlog
from sqlalchemy import select

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

# Publications are dense; a KOL posting 100 papers a year would swamp a report
# built from a handful of social posts. Capped per sweep, newest first.
_MAX_PER_KOL = 20
_MAX_PER_COMPETITOR = 15
_WINDOW_DAYS = 365


async def _save_documents(target_id: int, documents: list[dict], lane: str) -> int:
    """Store documents as ScrapedPost rows, skipping ones already held.

    Dedup is the content hash, the same key the scraper uses — re-running a
    sweep is therefore free and idempotent rather than duplicating a KOL's back
    catalogue every night.
    """
    from app.database import CelerySessionLocal
    from app.models import ScrapedPost
    from app.services.deduplicator import sha256_hash
    from app.services.fr_sources import Scope

    saved = 0
    async with CelerySessionLocal() as sess:
        for doc in documents:
            body = (doc.get("text") or "").strip()
            title = (doc.get("title") or "").strip()
            if not body and not title:
                continue
            content = f"{title}\n\n{body}".strip()
            digest = sha256_hash(content)

            exists = await sess.execute(
                select(ScrapedPost.id).where(ScrapedPost.content_hash == digest))
            if exists.scalars().first():
                continue

            sess.add(ScrapedPost(
                target_id=target_id,
                source_url=doc.get("url") or "",
                raw_content=content,
                title=title or None,
                content_hash=digest,
                idempotency_key=f"{lane}:{digest[:24]}",
                source_type=doc.get("kind") or lane,
                source_name=doc.get("source_name"),
                published_date=str(doc.get("published_date") or "")[:32] or None,
                # These are international registries, not French-hosted pages.
                # Scope reflects where the SOURCE lives, so calling a Europe PMC
                # record "fr" would corrupt the France share the client reads.
                # Relevance to France comes from the author being a tracked
                # French KOL, which the target link already records.
                source_scope=Scope.GLOBAL.value,
                source_category="publication" if lane == "publication" else "trial",
            ))
            saved += 1
        if saved:
            await sess.commit()
    return saved


def _queue_extraction(target_id: int) -> None:
    """Turn newly stored documents into insights.

    Without this the rows sit in scraped_posts and nothing surfaces: the KOL
    module, syntheses and reports all read ExtractedInsight, not ScrapedPost.

    run_id 0 is deliberate — these documents belong to no scrape run, and
    `patch_run` no-ops on a missing run, so the extractor's progress counters
    simply have nowhere to write.
    """
    try:
        from app.tasks.llm import extract_target_posts
        extract_target_posts.delay(target_id, 0)
    except Exception as exc:                        # noqa: BLE001 - queue down must not lose the documents
        logger.warning("literature.extract_queue_failed",
                       target_id=target_id, error=str(exc)[:160])


async def _run_publications(target_ids: list[int] | None = None) -> dict:
    from app.database import CelerySessionLocal
    from app.models import Target
    from app.services.literature import search_publications

    async with CelerySessionLocal() as sess:
        query = select(Target).where(Target.target_type == "kol")
        if target_ids:
            query = query.where(Target.id.in_(target_ids))
        kols = (await sess.execute(query.order_by(Target.name))).scalars().all()

    loop = asyncio.get_running_loop()
    total, reached = 0, 0
    for kol in kols:
        try:
            docs = await loop.run_in_executor(
                None, lambda k=kol: search_publications(
                    k.name, since_days=_WINDOW_DAYS, limit=_MAX_PER_KOL))
        except Exception as exc:                    # noqa: BLE001 - one KOL must not end the sweep
            logger.warning("literature.kol_failed", kol=kol.name, error=str(exc)[:160])
            continue
        if docs:
            reached += 1
            saved = await _save_documents(kol.id, docs, "publication")
            total += saved
            if saved:
                _queue_extraction(kol.id)

    logger.info("literature.publications_done", kols=len(kols), with_papers=reached, saved=total)
    return {"kols": len(kols), "with_papers": reached, "saved": total}


# Brand or affiliate name → the sponsor name registries actually file under.
# Verified against ClinicalTrials.gov trial counts, not assumed.
_SPONSOR_ALIASES = {
    "msd": "Merck Sharp & Dohme",
    "merck": "Merck Sharp & Dohme",
    "bms": "Bristol-Myers Squibb",
    "bristol myers squibb": "Bristol-Myers Squibb",
    "roche": "Hoffmann-La Roche",
    "genentech": "Genentech",
    "astrazeneca": "AstraZeneca",
    "sanofi": "Sanofi",
    "pfizer": "Pfizer",
    "novartis": "Novartis",
    "lilly": "Eli Lilly and Company",
    "j&j": "Janssen Research & Development",
    "janssen": "Janssen Research & Development",
}


def _registry_sponsor(target_name: str) -> str:
    """The sponsor string a trial registry will match for this company."""
    cleaned = (target_name or "").replace(" France", "").replace(" (test)", "").strip()
    return _SPONSOR_ALIASES.get(cleaned.lower(), cleaned)


async def _run_trials(target_ids: list[int] | None = None) -> dict:
    from app.database import CelerySessionLocal
    from app.models import Target
    from app.services.literature import search_trials

    async with CelerySessionLocal() as sess:
        query = select(Target).where(Target.target_type == "competitor")
        if target_ids:
            query = query.where(Target.id.in_(target_ids))
        companies = (await sess.execute(query.order_by(Target.name))).scalars().all()

    loop = asyncio.get_running_loop()
    total, reached = 0, 0
    for company in companies:
        # Registries record the legal entity, not the local affiliate or the
        # brand everyone uses. "MSD France" → "MSD" returns 2 trials; the same
        # company as "Merck Sharp & Dohme" returns 102. Stripping the country
        # is not enough, so known brands are mapped explicitly.
        sponsor = _registry_sponsor(company.name)
        try:
            docs = await loop.run_in_executor(
                None, lambda s=sponsor: search_trials(s, limit=_MAX_PER_COMPETITOR))
        except Exception as exc:                    # noqa: BLE001
            logger.warning("literature.trials_failed", sponsor=sponsor, error=str(exc)[:160])
            continue
        if docs:
            reached += 1
            saved = await _save_documents(company.id, docs, "trial")
            total += saved
            if saved:
                _queue_extraction(company.id)

    logger.info("literature.trials_done", companies=len(companies),
                with_trials=reached, saved=total)
    return {"companies": len(companies), "with_trials": reached, "saved": total}


@celery_app.task(
    bind=True,
    name="app.tasks.literature.sync_publications",
    queue="scrape",
    # Not acks_late: a redelivered sweep re-queries free APIs for content the
    # content-hash dedup would drop anyway, so the only cost is wasted time.
    acks_late=False,
    soft_time_limit=1800,
    time_limit=2100,
)
def sync_publications(self, target_ids: list[int] | None = None) -> dict:
    """Fetch recent papers for tracked KOLs."""
    return asyncio.run(_run_publications(target_ids))


@celery_app.task(
    bind=True,
    name="app.tasks.literature.sync_trials",
    queue="scrape",
    acks_late=False,
    soft_time_limit=1800,
    time_limit=2100,
)
def sync_trials(self, target_ids: list[int] | None = None) -> dict:
    """Fetch registry trials for tracked competitors."""
    return asyncio.run(_run_trials(target_ids))
