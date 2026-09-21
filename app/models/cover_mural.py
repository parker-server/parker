from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class CoverMural(Base):
    """A user-owned saved cover mural layout."""

    __tablename__ = "cover_murals"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)

    canvas_width = Column(Integer, nullable=False, default=1200)
    canvas_height = Column(Integer, nullable=False, default=900)
    grid_size = Column(Integer, nullable=False, default=24)
    background_color = Column(String, nullable=False, default="#111827")

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user = relationship("User", back_populates="cover_murals")
    items = relationship(
        "CoverMuralItem",
        back_populates="mural",
        cascade="all, delete-orphan",
        order_by="CoverMuralItem.z_index",
    )


class CoverMuralItem(Base):
    """A comic cover placed on a cover mural canvas."""

    __tablename__ = "cover_mural_items"

    id = Column(Integer, primary_key=True, index=True)
    mural_id = Column(Integer, ForeignKey("cover_murals.id", ondelete="CASCADE"), nullable=False, index=True)
    comic_id = Column(Integer, ForeignKey("comics.id", ondelete="CASCADE"), nullable=False, index=True)

    x = Column(Integer, nullable=False, default=0)
    y = Column(Integer, nullable=False, default=0)
    width = Column(Integer, nullable=False, default=192)
    height = Column(Integer, nullable=False, default=288)
    rotation = Column(Float, nullable=False, default=0)
    z_index = Column(Integer, nullable=False, default=0)
    fit_mode = Column(String, nullable=False, default="contain")

    mural = relationship("CoverMural", back_populates="items")
    comic = relationship("Comic", back_populates="cover_mural_items")
