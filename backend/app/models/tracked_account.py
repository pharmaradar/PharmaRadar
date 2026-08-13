"""Social accounts the platform monitors directly.

Keyword search is needle-in-a-haystack: you pay for N results and hope some are
relevant. Scraping a chosen account is harvesting a field you planted — every
post is on-topic and from a known voice by construction. That is why the client
asked for this, and why it is the highest-yield lever on French volume.

Until now the French accounts were a hardcoded tuple in services/fr_sources and
Facebook pages were a JSON blob in AppSettings, so the team could not add the
account they actually cared about. This makes the registry data.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Platforms an account can be tracked on. Instagram is listed because the
# registry should record the intent even though the current Apify actor is
# hashtag-only and cannot yet fetch a named profile — see the note in
# services/apify_client.
PLATFORMS = ("twitter", "linkedin", "instagram", "facebook")


class TrackedAccount(Base):
    __tablename__ = "tracked_accounts"

    __table_args__ = (
        # The same handle on two platforms is fine; twice on one is a duplicate
        # that would double-count in the voice distribution.
        UniqueConstraint("platform", "handle", name="uq_tracked_accounts_platform_handle"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # Bare handle for X/Instagram ("GustaveRoussy"); Facebook and LinkedIn are
    # addressed by URL, so `url` carries those and handle holds the slug.
    handle: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(String(255))
    # Free-text grouping shown in the UI: institution, pharma, patient association…
    category: Mapped[str | None] = mapped_column(String(32))
    # Who this actually is, and why we watch them. Free text on purpose: the
    # client knows their market better than an enum written months earlier.
    full_name: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    # kol | institution | pharma | patient_association | media | other
    role: Mapped[str | None] = mapped_column(String(32))

    # Scan health. Without these, "is this account being collected?" can only be
    # answered by reading worker logs — and a wrong handle is silent otherwise.
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # ok | empty | error. 'empty' means the scan ran and found nothing, which is
    # what a mistyped handle looks like from the outside.
    last_scan_status: Mapped[str | None] = mapped_column(String(16))
    post_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Cached AI read of what this account talks about. Costs an LLM call over
    # their posts, so it is written once and refreshed on request or when the
    # post count has moved past `analysis_post_count`.
    analysis_summary: Mapped[str | None] = mapped_column(Text)
    analysis_so_what: Mapped[str | None] = mapped_column(Text)
    analysis_themes: Mapped[str | None] = mapped_column(Text)   # JSON list[str]
    analysis_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    analysis_post_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Full six-section market-research analysis as JSON. The columns above hold
    # the headline fields the account card renders, so the list view never has
    # to parse this.
    analysis_sections: Mapped[str | None] = mapped_column(Text)

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
