from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.database import Base


class LibraryRoot(Base):
    __tablename__ = "library_roots"

    id = Column(Integer, primary_key=True, index=True)
    library_id = Column(Integer, ForeignKey("libraries.id", ondelete="CASCADE"), nullable=False, index=True)
    path = Column(String, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    last_scanned_at = Column(DateTime, nullable=True)
    last_scan_error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    library = relationship("Library", back_populates="roots")
