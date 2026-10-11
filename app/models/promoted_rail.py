from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Index, Integer, JSON, String

from app.database import Base


class PromotedRail(Base):
    """An administrator-curated, scheduled Home rail backed by an advanced search."""

    __tablename__ = "promoted_rails"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(120), nullable=False)
    query_config = Column(JSON, nullable=False, default=dict)
    starts_on = Column(Date, nullable=False)
    ends_on = Column(Date, nullable=True)
    enabled = Column(Boolean, nullable=False, default=False)
    display_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_promoted_rails_active_window", "enabled", "starts_on", "ends_on"),
    )
