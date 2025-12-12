from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import enum
from app.database import Base

class JobType(str, enum.Enum):
    SCAN = "scan"
    THUMBNAIL = "thumbnail"
    CLEANUP = "cleanup"

class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ScanJob(Base):
    __tablename__ = "scan_jobs"

    id = Column(Integer, primary_key=True, index=True)
    library_id = Column(Integer, ForeignKey("libraries.id"), nullable=True)

    job_type = Column(String, default=JobType.SCAN, index=True)

    # Status tracking
    status = Column(String, default=JobStatus.PENDING, index=True)
    force_scan = Column(Boolean, default=False)

    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Results & Diagnostics
    result_summary = Column(Text, nullable=True)  # JSON string of scan stats
    error_message = Column(Text, nullable=True)

    # Relationships
    library = relationship("Library")