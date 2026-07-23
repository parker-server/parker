from sqlalchemy import Column, Integer, String, DateTime, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.database import Base


class Library(Base):
    __tablename__ = "libraries"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    path = Column(String, nullable=False)
    scan_on_startup = Column(Boolean, default=False)
    watch_mode = Column(Boolean, default=False)  # Real-time watching
    parse_reading_lists = Column(Boolean, default=True, nullable=False)
    parse_collections = Column(Boolean, default=True, nullable=False)
    parse_story_arcs = Column(Boolean, default=True, nullable=False)

    last_scanned = Column(DateTime, nullable=True)
    is_scanning = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships - use string reference to avoid circular import
    series = relationship("Series", back_populates="library", cascade="all, delete-orphan")
    roots = relationship("LibraryRoot", back_populates="library", cascade="all, delete-orphan")
