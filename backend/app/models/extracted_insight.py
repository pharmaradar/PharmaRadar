from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import Column
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

CATEGORIES = [
    "roche", "other_pharma", "drug_approval", "clinical_trial",
    "pricing", "oncology", "research", "policy", "conference",
    "interview", "other",
]

WINDOW_TAGS = ["primary", "extended"]


class ExtractedInsight(Base):
    __tablename__ = "extracted_insights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scraped_post_id: Mapped[int] = mapped_column(Integer, ForeignKey("scraped_posts.id"), nullable=False, index=True)
    target_id: Mapped[int] = mapped_column(Integer, ForeignKey("targets.id"), nullable=False, index=True)

    topic: Mapped[str | None] = mapped_column(String(512))
    context: Mapped[str | None] = mapped_column(Text)
    what_they_said: Mapped[str | None] = mapped_column(Text)
    sentiment: Mapped[str | None] = mapped_column(String(32))   # positive / neutral / negative
    category: Mapped[str | None] = mapped_column(String(64))
    window_tag: Mapped[str] = mapped_column(String(32), default="primary")  # primary | extended

    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    embedding = Column(Vector(512), nullable=True)

    post: Mapped["ScrapedPost"] = relationship(back_populates="insights")  # noqa: F821
    target: Mapped["Target"] = relationship(back_populates="insights")  # noqa: F821
