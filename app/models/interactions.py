from sqlalchemy import Column, Integer, Boolean, ForeignKey, DateTime, CheckConstraint
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.database import Base


class UserSeries(Base):
    """
    Junction table for User <-> Series interactions.
    Stores 'Starred' (Want to Read) status.
    """
    __tablename__ = "user_series"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    series_id = Column(Integer, ForeignKey("series.id", ondelete="CASCADE"), primary_key=True)

    is_starred = Column(Boolean, default=False)
    starred_at = Column(DateTime, nullable=True)  # Sort by when they starred it

    # Relationships
    user = relationship("User", backref="series_preferences")
    series = relationship("Series", backref="user_preferences")


class UserComicRating(Base):
    """
    Junction table for User <-> Comic rating interactions.
    Stores one Parker rating per user per comic.
    """
    __tablename__ = "user_comic_ratings"
    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_user_comic_ratings_rating_range"),
    )

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    comic_id = Column(Integer, ForeignKey("comics.id", ondelete="CASCADE"), primary_key=True)
    rating = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    user = relationship("User", backref="comic_ratings")
    comic = relationship("Comic", backref="user_ratings")
