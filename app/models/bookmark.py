from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base


class Bookmark(Base):
    """Persist user-saved page markers without affecting resume progress."""

    __tablename__ = "bookmarks"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    comic_id = Column(Integer, ForeignKey("comics.id", ondelete="CASCADE"), nullable=False, index=True)
    page_index = Column(Integer, nullable=False)
    label = Column(String(120), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("user_id", "comic_id", "page_index", name="unique_user_comic_bookmark_page"),
    )

    comic = relationship("Comic", back_populates="bookmarks")
    user = relationship("User", back_populates="bookmarks")
