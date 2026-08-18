"""French market-access events on the drugs we track (BDPM / HAS / ANSM).

Two French regulatory signals that scraped content reports late and second-hand,
if at all, and that a French medical-affairs team treats as market-moving:

  ASMR opinions  the Commission de la Transparence's rating of a drug's added
                 clinical benefit, I (major) to V (none). It drives price and
                 reimbursement, so a competitor's ASMR is a commercial event —
                 durvalumab (Imfinzi, AstraZeneca) took ASMR IV in Feb 2026 on a
                 phase III superiority result, and Keytruda has 49 rulings on
                 file to Tecentriq's 10.

  Shortages      ANSM supply tensions and ruptures. A competitor being unable to
                 supply is an opportunity; our own shortage is a risk. Neither
                 shows up reliably in KOL posts.

Both come from the official public drug database, free and keyless, so this adds
a lane the client cares about without a vendor or a scraping question.

## Only tracked drugs are stored

The register covers 15,857 specialities across all of medicine. Storing all of
it would bury the signal in dermatology and vaccines. An event is kept only when
it matches a brand in services/brands.py — the Roche portfolio and the
competitor products the client named — which is the same registry that already
powers share-of-voice, so "what we track" has exactly one definition.
"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# asmr      Commission de la Transparence added-benefit rating (I-V)
# smr       actual-benefit rating (important / modéré / faible / insuffisant)
# shortage  ANSM supply tension or rupture
EVENT_KINDS = ("asmr", "smr", "shortage")


class MarketAccessEvent(Base):
    """One French regulatory event about a tracked drug."""

    __tablename__ = "market_access_events"

    __table_args__ = (
        Index("ix_market_access_brand_date", "brand", "event_date"),
        Index("ix_market_access_owner_date", "owner", "event_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # SHA256 over the identifying fields. The source files are full snapshots
    # re-published daily, so every sync re-reads rows it already has; the same
    # natural-key dedup scraped_posts.content_hash uses keeps that idempotent.
    # A CIS code alone would not do: one drug accumulates many rulings, and one
    # ruling covers many presentations of the same drug.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # The French drug identifier (Code Identifiant de Spécialité).
    cis_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # Full registry name, e.g. "TECENTRIQ 1 875 mg, solution injectable".
    drug_name: Mapped[str] = mapped_column(String(512), nullable=False)

    # Matched brand from services/brands.py, and who owns it ("roche", "MSD",
    # "AstraZeneca"…). `owner` is what makes "ours vs theirs" a query rather
    # than a string comparison in the UI.
    brand: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Marketing authorisation holder as filed — often a foreign entity
    # ("ROCHE REGISTRATION (ALLEMAGNE)"), so it is context, not the owner key.
    holder: Mapped[str | None] = mapped_column(String(255))

    # ASMR: "I".."V". SMR: the French wording. Shortage: the ANSM status text.
    rating: Mapped[str | None] = mapped_column(String(64), index=True)
    # The Commission de la Transparence opinion id, e.g. "CT-21782".
    opinion_ref: Mapped[str | None] = mapped_column(String(32))

    event_date: Mapped[date | None] = mapped_column(Date, index=True)
    # Shortages carry an expected end date; rulings do not.
    end_date: Mapped[date | None] = mapped_column(Date)

    # The register's own reasoning text. Kept verbatim in French: it is the
    # primary source a medical-affairs reader wants, and translating or
    # summarising it here would put a paraphrase where a citation belongs.
    summary: Mapped[str | None] = mapped_column(Text)
    # HAS opinion page for rulings, ANSM availability page for shortages.
    url: Mapped[str | None] = mapped_column(Text)

    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
