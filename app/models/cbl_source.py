from sqlalchemy import Column, Integer, String, Text, DateTime
from datetime import datetime, timezone
from app.database import Base


class CBLSource(Base):
    """A managed .cbl file Parker has imported (upload, URL, catalog, or library-root discovery)."""
    __tablename__ = "cbl_sources"

    id = Column(Integer, primary_key=True, index=True)
    display_name = Column(String, nullable=False)
    stored_path = Column(String, nullable=False)
    original_filename = Column(String, nullable=True)

    # "upload", "url", "catalog", or "library_import"
    origin = Column(String, nullable=False, index=True)
    source_url = Column(String, nullable=True)
    catalog_provider = Column(String, nullable=True)
    catalog_path = Column(String, nullable=True)

    imported_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_refreshed_at = Column(DateTime, nullable=True)
    last_refresh_status = Column(String, nullable=False, default="never")

    fingerprint = Column(String, nullable=False, index=True)
    entry_count = Column(Integer, nullable=True)
    last_match_summary = Column(Text, nullable=True)
