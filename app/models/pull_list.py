from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, UniqueConstraint, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class PullList(Base):
    __tablename__ = "pull_lists"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="pull_lists")

    # One-to-Many to the items
    items = relationship("PullListItem", back_populates="pull_list", cascade="all, delete-orphan",
                         order_by="PullListItem.sort_order") # Auto-sort by custom order


class PullListItem(Base):
    """
        The 'Junction' table.
        It links a List to a Comic, but adds 'sort_order' so the user can curate the sequence.
    """
    __tablename__ = "pull_list_items"

    id = Column(Integer, primary_key=True, index=True)
    pull_list_id = Column(Integer, ForeignKey("pull_lists.id"), nullable=False)
    comic_id = Column(Integer, ForeignKey("comics.id"), nullable=False)

    # Critical for custom ordering
    sort_order = Column(Integer, nullable=False, default=0)

    pull_list = relationship("PullList", back_populates="items")
    comic = relationship("Comic") # Unidirectional access to comic data

    # Constraint: Ensure a comic appears only once per list (optional, but good for sanity)
    __table_args__ = (
        UniqueConstraint('pull_list_id', 'comic_id', name='uq_pull_list_item'),
    )