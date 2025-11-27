from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class Series(Base):
    __tablename__ = "series"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    library_id = Column(Integer, ForeignKey("libraries.id"))

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    library = relationship("Library", back_populates="series")
    volumes = relationship("Volume", back_populates="series", cascade="all, delete-orphan")