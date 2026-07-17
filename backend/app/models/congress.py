"""Congress tracking models and the questions answered by each report."""
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Congress(Base):
    __tablename__ = "congresses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashtags: Mapped[str | None] = mapped_column(Text)  # JSON list[str]
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    disease_area: Mapped[str | None] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    questions: Mapped[list["CongressQuestion"]] = relationship(
        back_populates="congress", lazy="select", cascade="all, delete-orphan"
    )
    reports: Mapped[list["BurningTopicReport"]] = relationship(
        back_populates="congress", lazy="select", cascade="all, delete-orphan"
    )


class CongressQuestion(Base):
    __tablename__ = "congress_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    congress_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("congresses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    congress: Mapped[Congress] = relationship(back_populates="questions")
