from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RunStatus:
    running = "running"
    success = "success"
    error = "error"
    cancelled = "cancelled"


class RunLog(Base):
    __tablename__ = "run_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)

    status: Mapped[str] = mapped_column(String(32), default=RunStatus.running, nullable=False, index=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    total_targets: Mapped[int] = mapped_column(Integer, default=0)
    targets_processed: Mapped[int] = mapped_column(Integer, default=0)
    new_posts_found: Mapped[int] = mapped_column(Integer, default=0)
    duplicates_skipped: Mapped[int] = mapped_column(Integer, default=0)
    insights_extracted: Mapped[int] = mapped_column(Integer, default=0)
    pdfs_generated: Mapped[int] = mapped_column(Integer, default=0)

    current_target: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    llm_calls_used: Mapped[int] = mapped_column(Integer, default=0)
