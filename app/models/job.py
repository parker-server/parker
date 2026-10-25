from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Boolean, Index, text
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import enum
from app.database import Base

class JobType(str, enum.Enum):
    SCAN = "scan"
    THUMBNAIL = "thumbnail"
    CLEANUP = "cleanup"
    METADATA_REHYDRATE = "metadata_rehydrate"
    EXTERNAL_REVIEW_LOOKUP = "external_review_lookup"

class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# Each lane gets its own worker thread. Within a lane, job types are listed in
# priority order: the worker always claims the first type that has pending work.
LIBRARY_LANE = "library"
ENRICHMENT_LANE = "enrichment"

JOB_LANES: dict[str, tuple[JobType, ...]] = {
    LIBRARY_LANE: (JobType.SCAN, JobType.THUMBNAIL, JobType.CLEANUP, JobType.METADATA_REHYDRATE),
    ENRICHMENT_LANE: (JobType.EXTERNAL_REVIEW_LOOKUP,),
}

JOB_TYPE_LANES: dict[str, str] = {
    job_type.value: lane
    for lane, lane_job_types in JOB_LANES.items()
    for job_type in lane_job_types
}


class ScanJob(Base):
    __tablename__ = "scan_jobs"
    __table_args__ = (
        # Only one active external review lookup per target. Enforced by the
        # database so concurrent Uvicorn workers cannot enqueue duplicates.
        Index(
            "uq_scan_jobs_active_external_review",
            "job_type",
            "target_type",
            "target_id",
            unique=True,
            sqlite_where=text("job_type = 'external_review_lookup' AND status IN ('pending', 'running')"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    library_id = Column(Integer, ForeignKey("libraries.id"), nullable=True)

    job_type = Column(String, default=JobType.SCAN, index=True)

    # Generic target for jobs that are not scoped to a library (e.g. a comic).
    target_type = Column(String, nullable=True)
    target_id = Column(Integer, nullable=True)
    payload_json = Column(Text, nullable=True)  # JSON string of job-specific options

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
