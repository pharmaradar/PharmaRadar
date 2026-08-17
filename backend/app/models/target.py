from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Target(Base):
    __tablename__ = "targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    # JSON-encoded list of known URLs / handles
    known_urls: Mapped[str | None] = mapped_column(Text, default="[]")
    notes: Mapped[str | None] = mapped_column(Text)
    disease_area: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 'kol' | 'competitor' — same scrape/extract pipeline, separated at
    # synthesis/brief level so competitor content never bleeds into KOL briefs.
    target_type: Mapped[str] = mapped_column(String(16), nullable=False, default="kol", index=True)
    twitter_handle: Mapped[str | None] = mapped_column(String(128), nullable=True)  # e.g. @DrJohnSmith
    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)    # full profile URL
    # Acquisition scope: 'fr' pins search at French sources (locale flags +
    # curated site: groups), 'global' searches worldwide. Defaults to 'fr' —
    # the platform monitors the French market, and the client asked for
    # competitor messaging in French too. See services/fr_sources.py.
    source_scope: Mapped[str] = mapped_column(String(8), nullable=False, default="fr",
                                              server_default="fr")
    # ── Transparence Santé (French Sunshine Act) ──────────────
    # Pinned national health-professional id. Identity is resolved ONCE to this
    # and every payment query filters on it; see services/transparence.py for
    # why a name can never be the key. NULL until resolution succeeds, and
    # payments are shown ONLY when transparence_status == "resolved" — an
    # ambiguous match displays nothing rather than another clinician's money.
    transparence_rpps: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # unresolved | resolved | ambiguous | not_found (RESOLUTION_STATES)
    transparence_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unresolved", index=True)
    # Share of identified declarations backing the pin — shown so a reader can
    # see how strong the match is instead of trusting it blindly.
    transparence_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Human-readable reason, especially for the refusals ("3 RPPS share this
    # name; top one holds only 44%").
    transparence_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    transparence_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Last successful payment sync. Surfaced in the UI so a figure is never read
    # as current when the sync has been failing.
    transparence_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    posts: Mapped[list["ScrapedPost"]] = relationship(back_populates="target", lazy="select")  # noqa: F821
    insights: Mapped[list["ExtractedInsight"]] = relationship(back_populates="target", lazy="select")  # noqa: F821
    summaries: Mapped[list["PersonSummary"]] = relationship(back_populates="target", lazy="select")  # noqa: F821
