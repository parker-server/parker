from sqlalchemy import Column, Integer, String, ForeignKey, Text, DateTime, Float, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class ReadingList(Base):
    """A reading list (often for crossover events)"""
    __tablename__ = "reading_lists"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)
    description = Column(Text)

    # Track if this was auto-generated from AlternateSeries
    auto_generated = Column(Integer, default=1)  # SQLite uses 1/0 for boolean

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship to items
    items = relationship("ReadingListItem", back_populates="reading_list",
                         cascade="all, delete-orphan", order_by="ReadingListItem.position")


class ReadingListItem(Base):
    """A comic in a reading list with a specific position/order"""
    __tablename__ = "reading_list_items"

    id = Column(Integer, primary_key=True, index=True)
    reading_list_id = Column(Integer, ForeignKey('reading_lists.id', ondelete='CASCADE'), nullable=False)
    comic_id = Column(Integer, ForeignKey('comics.id', ondelete='CASCADE'), nullable=False)

    # Position in the reading order (from AlternateNumber or manual)
    position = Column(Float, nullable=False)  # Float to allow 1.5, 2.5 for insertions

    # Prevent duplicate comics in same list
    __table_args__ = (
        UniqueConstraint('reading_list_id', 'comic_id', name='unique_list_comic'),
    )

    # Relationships
    reading_list = relationship("ReadingList", back_populates="items")
    comic = relationship("Comic", back_populates="reading_list_items")