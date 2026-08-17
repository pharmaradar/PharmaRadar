"""Industry payments to French healthcare professionals (Transparence Santé).

France's Sunshine Act register, published by the Ministry of Health: every
convention, fee and benefit a company grants a healthcare professional, with the
amount and the stated reason. 10.1M records, free public API, no key.

Why it matters here: it answers two questions the platform could not answer from
scraped content. Which competitor is investing in which French KOL, and how much
— the competitive-intelligence section the client left blank in his spec. And
who the industry treats as influential, which is an external signal, unlike our
current ranking by how much a person happens to post.

## Identity is pinned to RPPS, never to a name

The register is filed by thousands of companies with no shared spelling
convention, so the same person appears many ways. Prof. Barlési, one of the
tracked KOLs, appears under six city spellings — VILLEJUIF CEDEX, Villejuif,
VILLEJUIF, MARSEILLE, MARSEILLE CEDEX 05, MARSEILLE--5E--ARRONDI — because he
moved institution and because formatting is inconsistent. Meanwhile "MARTIN"
covers many unrelated physicians in different cities.

So city cannot disambiguate (it fragments one person AND legitimately changes),
and a name alone cannot identify (it collides). What is stable is the RPPS, the
French national health-professional identifier: 7.7M of the 10.1M records carry
one, and 224 of Barlési's 269 records share the single RPPS 10003416467.

A target is therefore resolved ONCE to an RPPS, and every payment query filters
on that id. Spelling, accents, CEDEX variants and moving hospital all stop
mattering. Where resolution is not confident, the target stays unresolved and
the UI shows nothing rather than a number that might belong to someone else — a
payment attributed to the wrong clinician in a client-facing competitive brief
is far worse than an absent one.
"""
from datetime import date, datetime

from sqlalchemy import (
    Date, DateTime, Float, ForeignKey, Integer, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Resolution states for Target.transparence_status.
#   unresolved — not looked up yet
#   resolved   — pinned to exactly one RPPS; payments are safe to display
#   ambiguous  — several plausible people; we refuse to choose, show nothing
#   not_found  — searched, nobody in the register matches (fine: not every
#                target is an individual French HCP — competitors are companies)
RESOLUTION_STATES = ("unresolved", "resolved", "ambiguous", "not_found")


class TransparencePayment(Base):
    """One declared payment, mirrored locally so briefs do not depend on a live
    third-party call, and so amounts can be aggregated in SQL alongside insights.

    Mirrored rather than queried live because the register is a public record of
    what was *declared* — a row does not change once published, it is superseded
    by a new one. The sync is therefore append-mostly and incremental on
    `published_on`.
    """

    __tablename__ = "transparence_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # The register's own record id. Unique so a re-sync of an overlapping window
    # is idempotent — same natural-key dedup as scraped_posts.content_hash.
    declaration_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    target_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("targets.id", ondelete="CASCADE"), nullable=False, index=True)

    # Copied onto the row so a payment can always be traced back to the identity
    # it was matched on, even if the target is later re-resolved to a different
    # RPPS. Without this a mis-resolution would be invisible after the fact.
    rpps: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # Who paid. `company_siren` is the stable French company id; raison_sociale
    # is the filed trade name and varies ("ROCHE SAS", "ROCHE").
    company: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    company_siren: Mapped[str | None] = mapped_column(String(32), index=True)

    amount_eur: Mapped[float] = mapped_column(Float, nullable=False)
    paid_on: Mapped[date | None] = mapped_column(Date, index=True)

    # 'convention' | 'remuneration' | 'avantage' — the three declaration kinds.
    kind: Mapped[str | None] = mapped_column(String(32), index=True)
    # Free text from the register: "Contrat d'intervenant", "Frais de transport"…
    reason: Mapped[str | None] = mapped_column(String(255))

    # When the register published it. This is the incremental sync cursor, and
    # it is NOT the payment date — a 2017 payment can be published in 2026.
    published_on: Mapped[date | None] = mapped_column(Date, index=True)

    # Kept for display context only. Deliberately never used for matching: see
    # the module docstring on why city fragments a single person.
    city: Mapped[str | None] = mapped_column(String(128))

    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
