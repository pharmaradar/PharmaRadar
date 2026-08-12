from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SocialPost(Base):
    """A post scraped from a social platform via Apify for trend tracking.

    Separate from ScrapedPost (the KOL insight pipeline) so social trend data
    with real engagement counts doesn't pollute KOL stats.
    """
    __tablename__ = "social_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    platform: Mapped[str] = mapped_column(String(32), index=True)   # instagram | twitter | linkedin | facebook
    post_url: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String(255))
    text: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)

    # Engagement — real counts from the platform
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    views: Mapped[int] = mapped_column(Integer, default=0)
    shares: Mapped[int] = mapped_column(Integer, default=0)

    hashtags: Mapped[str | None] = mapped_column(Text)              # JSON list
    query: Mapped[str | None] = mapped_column(String(255), index=True)  # hashtag/keyword/handle that found it
    kind: Mapped[str] = mapped_column(String(16), default="field")  # kol | field | account | comment
    disease_area: Mapped[str | None] = mapped_column(String(64), index=True)
    topic: Mapped[str | None] = mapped_column(String(255))          # coarse topic (from query/hashtag)

    language: Mapped[str | None] = mapped_column(String(8))           # 'en' | 'fr' | etc.
    # Set on comments: the post they were left under. Comments are stored as
    # SocialPost rows on purpose — that way they inherit AE classification, the
    # dedup hash, language detection and every read-time filter, instead of a
    # parallel table that could quietly miss the pharmacovigilance guarantee.
    parent_url: Mapped[str | None] = mapped_column(Text, index=True)
    # Provenance — which source the post came from, recorded at ingest.
    # Distinct from `language`, which is inferred from the text after the fact.
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    source_scope: Mapped[str | None] = mapped_column(String(8), index=True)   # 'fr' | 'global'
    llm_description: Mapped[str | None] = mapped_column(Text)        # filled on click

    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    # Pharmacovigilance — same semantics as ScrapedPost.is_adverse_event.
    is_adverse_event: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ae_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
