from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ScrapedPost(Base):
    __tablename__ = "scraped_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[int] = mapped_column(Integer, ForeignKey("targets.id"), nullable=False, index=True)

    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(64))   # twitter, linkedin, news, ...
    source_name: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(Text)
    raw_content: Mapped[str | None] = mapped_column(Text)
    published_date: Mapped[str | None] = mapped_column(String(32))  # ISO date string

    # SHA256 of normalised content — primary dedup key
    content_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)

    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)

    target: Mapped["Target"] = relationship(back_populates="posts")  # noqa: F821
    insights: Mapped[list["ExtractedInsight"]] = relationship(back_populates="post", lazy="select")  # noqa: F821

    __table_args__ = (
        Index("ix_scraped_posts_target_scraped", "target_id", "scraped_at"),
    )
