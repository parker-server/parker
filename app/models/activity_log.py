from sqlalchemy import Column, Integer, ForeignKey, DateTime, String
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.database import Base


class ActivityLog(Base):
    """Stores granular reading session data for velocity and heatmaps"""
    __tablename__ = "activity_log"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    comic_id = Column(Integer, ForeignKey("comics.id", ondelete="CASCADE"), nullable=False, index=True)

    # Delta tracking: how many pages were turned in this specific save event
    pages_read = Column(Integer, nullable=False, default=0)
    start_page = Column(Integer, nullable=False)
    end_page = Column(Integer, nullable=False)

    # Context (e.g., 'series', 'pull_list') to track WHERE the user is reading from
    context_type = Column(String, nullable=True)
    context_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    # Relationships
    comic = relationship("Comic")
    user = relationship("User", back_populates="activity_logs")
