from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
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
    twitter_handle: Mapped[str | None] = mapped_column(String(128), nullable=True)  # e.g. @DrJohnSmith
    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)    # full profile URL
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    posts: Mapped[list["ScrapedPost"]] = relationship(back_populates="target", lazy="select")  # noqa: F821
    insights: Mapped[list["ExtractedInsight"]] = relationship(back_populates="target", lazy="select")  # noqa: F821
    summaries: Mapped[list["PersonSummary"]] = relationship(back_populates="target", lazy="select")  # noqa: F821
