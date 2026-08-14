"""Burning Topics — persistent user-defined topic tracker.

Distinct from the ad-hoc Topic Explorer: topics are durable DB rows, and each
"generate report" produces a BurningTopicReport row (synthesis + PDF) that
stays browsable/downloadable afterwards.
"""
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class BurningTopic(Base):
    __tablename__ = "burning_topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    language_filter: Mapped[str | None] = mapped_column(String(8))   # 'fr' | 'en' | None = all
    period_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    exclusion_words: Mapped[str | None] = mapped_column(Text)        # JSON list — posts containing any are dropped
    restriction_terms: Mapped[str | None] = mapped_column(Text)      # JSON list — extra match terms besides name
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    reports: Mapped[list["BurningTopicReport"]] = relationship(
        back_populates="topic", lazy="select", cascade="all, delete-orphan")


class BurningTopicReport(Base):
    __tablename__ = "burning_topic_reports"

    __table_args__ = (
        CheckConstraint(
            "(topic_id IS NOT NULL AND congress_id IS NULL) OR "
            "(topic_id IS NULL AND congress_id IS NOT NULL)",
            name="ck_burning_topic_reports_one_owner",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("burning_topics.id", ondelete="CASCADE"),
        nullable=True, index=True)
    congress_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("congresses.id", ondelete="CASCADE"),
        nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending|running|done|failed
    summary_md: Mapped[str | None] = mapped_column(Text)
    key_findings: Mapped[str | None] = mapped_column(Text)      # JSON list[str]
    so_what: Mapped[str | None] = mapped_column(Text)
    important_posts: Mapped[str | None] = mapped_column(Text)   # JSON list[{url,title,author,engagement,platform,why}]
    main_authors: Mapped[str | None] = mapped_column(Text)      # JSON list[{author,posts,engagement,platforms,note}]

    # Market-research sections (client v1 feedback). Nullable so reports created
    # before these existed still render — the UI shows a section only when it
    # has content.
    what_is_said: Mapped[str | None] = mapped_column(Text)
    voices_note: Mapped[str | None] = mapped_column(Text)
    volume_note: Mapped[str | None] = mapped_column(Text)
    subtopics: Mapped[str | None] = mapped_column(Text)         # JSON list[str]
    # Voice distribution and volume are COUNTED from the candidate rows, not
    # written by the model — see services/voice_profile for why that matters.
    voice_rows: Mapped[str | None] = mapped_column(Text)        # JSON list[{bucket,label,mentions,percent}]
    volume: Mapped[str | None] = mapped_column(Text)            # JSON dict
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    voice_exact_share: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # The window this report was actually generated over, so it is not reread
    # against whatever the topic's period_days happens to be later.
    window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30, server_default="30")
    question_answers: Mapped[str | None] = mapped_column(Text)  # JSON list[{question_id,question,answer}]
    pdf_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    topic: Mapped["BurningTopic"] = relationship(back_populates="reports")
    congress: Mapped["Congress"] = relationship(back_populates="reports")  # noqa: F821
