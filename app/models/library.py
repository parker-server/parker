from sqlalchemy import Column, Integer, String, DateTime, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class Library(Base):
    __tablename__ = "libraries"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    path = Column(String, nullable=False)
    scan_on_startup = Column(Boolean, default=False)
    watch_mode = Column(Boolean, default=False)  # Real-time watching

    last_scanned = Column(DateTime, nullable=True)
    is_scanning = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships - use string reference to avoid circular import
    series = relationship("Series", back_populates="library", cascade="all, delete-orphan")