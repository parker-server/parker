from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Index, Integer, String, Text

from app.database import Base


class Notification(Base):
    """
    One notification for one user.

    Rows are written only through NotificationService. Title, body and link are
    rendered from the kind's registry entry when the row is created or grouped.
    """
    __tablename__ = "notifications"

    __table_args__ = (
        Index("idx_notifications_user_read", "user_id", "read_at"),
        Index("idx_notifications_user_group", "user_id", "group_key"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String, nullable=False)
    title = Column(String, nullable=False)
    body = Column(Text, nullable=True)
    link = Column(String, nullable=True)
    group_key = Column(String, nullable=True)
    count = Column(Integer, default=1, nullable=False)
    data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    read_at = Column(DateTime, nullable=True)
