from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SearchHistory(Base):
    """Per-user record of what a user searched. The actual results live in the
    shared cache tables (DiscoveryResult / SocialPost) so cost is never doubled;
    this only tracks who searched what, for a per-user history view."""

    __tablename__ = "search_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # discovery | social
    query: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
