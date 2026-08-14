"""Ad-hoc market-research reports produced by Topic Explorer.

Deliberately a separate table from `burning_topic_reports` rather than a third
owner column on it. That table carries a CHECK constraint allowing exactly one
owner — `topic_id` XOR `congress_id` — and relaxing it to admit a free-text
question would weaken a constraint that currently protects a working feature.

These reports are also a different kind of thing: a Burning Topic is a durable
named subject the team tracks over time, while this is the answer to one
question somebody asked once. Both render through the same generator
(services/market_report), which is where the shared logic belongs.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MarketReport(Base):
    __tablename__ = "market_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending|running|done|failed
    error: Mapped[str | None] = mapped_column(Text)

    # Scope the report was generated under, so a stored report can be read back
    # with the filters that produced it rather than today's defaults.
    window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    language: Mapped[str | None] = mapped_column(String(8), default="fr")

    # The six required sections. Prose is stored as text; anything list- or
    # table-shaped is JSON so the UI and the PDF read the same structure.
    exec_summary: Mapped[str | None] = mapped_column(Text)
    so_what: Mapped[str | None] = mapped_column(Text)
    what_is_said: Mapped[str | None] = mapped_column(Text)
    voices_note: Mapped[str | None] = mapped_column(Text)
    volume_note: Mapped[str | None] = mapped_column(Text)
    subtopics: Mapped[str | None] = mapped_column(Text)        # JSON list[str]
    voice_rows: Mapped[str | None] = mapped_column(Text)       # JSON list[{bucket,label,mentions,percent}]
    # JSON list[{author,mentions,engagement,platforms,tracked}] — the main
    # speakers, replacing the standalone emerging-voices panel.
    main_authors: Mapped[str | None] = mapped_column(Text)
    volume: Mapped[str | None] = mapped_column(Text)           # JSON dict
    key_posts: Mapped[str | None] = mapped_column(Text)        # JSON list[dict]
    sources: Mapped[str | None] = mapped_column(Text)          # JSON list[{n,author,url,...}]

    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    voice_exact_share: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pdf_url: Mapped[str | None] = mapped_column(Text)

    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
