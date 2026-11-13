from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class ProblemReport(Base):
    """
    A reader's note to the admin about something wrong with a comic.

    Reports never change archives or metadata. When the comic is deleted the
    report is kept: comic_id becomes null and comic_label still says what it was.
    When the reporter's account is deleted the report is kept too, with user_id
    cleared, since the problem with the comic is still real.
    """
    __tablename__ = "problem_reports"

    __table_args__ = (
        Index("idx_problem_reports_comic_status", "comic_id", "status"),
    )

    id = Column(Integer, primary_key=True)
    comic_id = Column(Integer, ForeignKey("comics.id", ondelete="SET NULL"), nullable=True, index=True)
    # Series, number and relative path at creation, for display after deletion.
    comic_label = Column(String, nullable=False)
    # Null after the reporter's account is deleted.
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    category = Column(String, nullable=False)
    # Zero-based, matching the reader; shown one-based.
    page_index = Column(Integer, nullable=True)
    message = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="open", index=True)
    admin_note = Column(Text, nullable=True)
    resolved_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Set by the scanner when the comic's file changes while the report is open.
    file_changed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    resolved_at = Column(DateTime, nullable=True)

    comic = relationship("Comic", back_populates="problem_reports")
    user = relationship("User", foreign_keys=[user_id], back_populates="problem_reports")
    resolved_by = relationship("User", foreign_keys=[resolved_by_id], back_populates="resolved_problem_reports")
