"""Adverse-Event visibility filters — used by EVERY human-visible query.

Regulatory rule: posts classified as adverse-event reports stay in the DB but
must never be shown to a human — not in lists, briefs, syntheses, reports,
PDFs, agent answers, or topic/congress reports. Filtering happens at read
time; rows are never deleted.

`IS NOT TRUE` semantics: NULL (unclassified) rows remain visible — only a
positive classification hides a post. The backfill sweep + inline extractor
classification converge every post to a definite True/False.

Usage:
    .where(post_not_ae())      # query selects/joins ScrapedPost
    .where(social_not_ae())    # query selects SocialPost
    .where(insight_not_ae())   # query selects ExtractedInsight WITHOUT joining
                               # ScrapedPost (adds a correlated NOT-EXISTS)
"""
from __future__ import annotations

from sqlalchemy import select


def post_not_ae():
    from app.models import ScrapedPost
    return ScrapedPost.is_adverse_event.is_not(True)


def social_not_ae():
    from app.models import SocialPost
    return SocialPost.is_adverse_event.is_not(True)


def insight_not_ae():
    """An insight extracted from an AE post is AE-derived — hide it too."""
    from app.models import ExtractedInsight, ScrapedPost
    return ~(
        select(ScrapedPost.id)
        .where(
            ScrapedPost.id == ExtractedInsight.scraped_post_id,
            ScrapedPost.is_adverse_event.is_(True),
        )
        .exists()
    )
