from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


class ExternalReviewLookup(Base):
    __tablename__ = "external_review_lookups"

    __table_args__ = (
        UniqueConstraint("comic_id", "provider", name="uq_external_review_lookup_comic_provider"),
        Index("idx_external_review_lookup_status_retry", "status", "next_retry_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    comic_id = Column(Integer, ForeignKey("comics.id"), nullable=False, index=True)
    provider = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="pending", index=True)
    source_issue_url = Column(Text, nullable=True)
    confidence_score = Column(Float, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    next_retry_at = Column(DateTime, nullable=True, index=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    comic = relationship("Comic")
    reviews = relationship(
        "ExternalReview",
        back_populates="lookup",
        cascade="all, delete-orphan",
        order_by="ExternalReview.sort_order",
    )


class ExternalReview(Base):
    __tablename__ = "external_reviews"

    id = Column(Integer, primary_key=True, index=True)
    lookup_id = Column(Integer, ForeignKey("external_review_lookups.id"), nullable=False, index=True)
    review_type = Column(String, nullable=False, default="critic", index=True)
    source_name = Column(String, nullable=False)
    author = Column(String, nullable=True)
    score = Column(Float, nullable=True)
    review_date = Column(Date, nullable=True)
    excerpt = Column(Text, nullable=True)
    full_review_url = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    lookup = relationship("ExternalReviewLookup", back_populates="reviews")
