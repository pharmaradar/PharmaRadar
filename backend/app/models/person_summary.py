from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PersonSummary(Base):
    __tablename__ = "person_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[int] = mapped_column(Integer, ForeignKey("targets.id"), nullable=False, index=True)
    run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("run_logs.id"), index=True)

    # JSON-encoded list of bullet strings
    summary_bullets: Mapped[str | None] = mapped_column(Text)
    so_what_pharma: Mapped[str | None] = mapped_column(Text)
    insights_count: Mapped[int] = mapped_column(Integer, default=0)

    # Extended-window fallback summaries (populated when primary window is empty)
    summary_bullets_extended: Mapped[str | None] = mapped_column(Text)
    so_what_pharma_extended: Mapped[str | None] = mapped_column(Text)

    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    target: Mapped["Target"] = relationship(back_populates="summaries")  # noqa: F821
