from datetime import datetime
from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class DiscoveryResult(Base):
    __tablename__ = "discovery_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query: Mapped[str] = mapped_column(String(512), index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    source_name: Mapped[str | None] = mapped_column(String(255))
    published_date: Mapped[str | None] = mapped_column(String(32))
    content_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    media_type: Mapped[str] = mapped_column(String(32), default="article")  # article | video | pdf
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(8))
    llm_description: Mapped[str | None] = mapped_column(Text)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
