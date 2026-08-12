from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ScrapedPost(Base):
    __tablename__ = "scraped_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[int] = mapped_column(Integer, ForeignKey("targets.id"), nullable=False, index=True)

    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(64))   # twitter, linkedin, news, ...
    source_name: Mapped[str | None] = mapped_column(String(255))
    # Provenance, derived from source_url at save time (services/scraper.py).
    # The client's requirement is a SOURCE requirement — "most of my sources from
    # France" — so which source a post came from has to be a stored fact, not
    # re-derived from its text. See services/fr_sources.py.
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    source_scope: Mapped[str | None] = mapped_column(String(8), index=True)   # 'fr' | 'global'
    source_category: Mapped[str | None] = mapped_column(String(32))           # institution, medical_press, …
    title: Mapped[str | None] = mapped_column(Text)
    raw_content: Mapped[str | None] = mapped_column(Text)
    published_date: Mapped[str | None] = mapped_column(String(32))  # ISO date string

    # SHA256 of normalised content — primary dedup key
    content_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)

    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Pharmacovigilance: NULL = not yet classified, True = adverse-event report
    # (specific patient harmed by a drug). True rows are stored but NEVER shown
    # to a human — see services/ae_filter.py. Never delete these rows.
    is_adverse_event: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ae_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)

    target: Mapped["Target"] = relationship(back_populates="posts")  # noqa: F821
    insights: Mapped[list["ExtractedInsight"]] = relationship(back_populates="post", lazy="select")  # noqa: F821

    __table_args__ = (
        Index("ix_scraped_posts_target_scraped", "target_id", "scraped_at"),
    )
