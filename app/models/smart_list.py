from sqlalchemy import Column, Integer, String, ForeignKey, Boolean, JSON, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.database import Base


class SmartList(Base):
    """
    A dynamic list defined by a search query (JSON).
    "Auto-fires" to populate a rail based on live data.
    """
    __tablename__ = "smart_lists"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    name = Column(String, nullable=False)  # e.g., "Alan Moore's Swamp Thing"
    description = Column(String, nullable=True)
    icon = Column(String, default="⚡")  # Emoji or Icon class

    # Stores the exact JSON payload used by SearchService
    # { "match": "all", "filters": [...], "sort_by": "year" }
    query_config = Column(JSON, nullable=False)

    # UI Config
    show_on_dashboard = Column(Boolean, default=True)
    show_in_library = Column(Boolean, default=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    user = relationship("User", backref="smart_lists")