import json
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import asc, case, desc, or_
from sqlalchemy.orm import Session, joinedload

from app.api.deps import SessionDep, AdminUser
from app.models.comic import Comic, Volume
from app.models.job import ScanJob, JobStatus, JobType, JOB_LANES, JOB_TYPE_LANES, LIBRARY_LANE
from app.models.library import Library
from app.models.series import Series
from app.services.external_reviews import issue_display_title

router = APIRouter()

# Pending jobs are listed lane by lane, in each lane's claim order.
JOB_TYPE_PRIORITY = {
    job_type.value: index
    for index, job_type in enumerate(
        job_type for lane_job_types in JOB_LANES.values() for job_type in lane_job_types
    )
}

LIBRARY_JOB_TYPES = [job_type.value for job_type in JOB_LANES[LIBRARY_LANE]]
ENRICHMENT_JOB_TYPES = [
    job_type.value
    for lane, lane_job_types in JOB_LANES.items()
    if lane != LIBRARY_LANE
    for job_type in lane_job_types
]


def job_type_value(job_type: JobType | str | None) -> str:
    if isinstance(job_type, JobType):
        return job_type.value
    return job_type or JobType.SCAN.value


def status_value(status: JobStatus | str | None) -> str:
    if isinstance(status, JobStatus):
        return status.value
    return status or JobStatus.PENDING.value


def library_lane_filter():
    """Library-lane jobs, including legacy rows with no job type (treated as scans)."""
    return or_(ScanJob.job_type.is_(None), ScanJob.job_type.in_(LIBRARY_JOB_TYPES))


def determine_library_name(job_type: JobType | str | None, job_library: Library | None) -> str:
    if job_type_value(job_type) in ENRICHMENT_JOB_TYPES:
        # Enrichment jobs target a single item, not a library
        library_name = "-"
    elif job_type_value(job_type) == JobType.CLEANUP.value and not job_library:
        library_name = "-"
    elif not job_library:
        library_name = "Deleted Library"
    else:
        library_name = job_library.name

    return library_name


def duration_seconds(started_at: datetime | None, completed_at: datetime | None) -> float | None:
    if not started_at or not completed_at:
        return None
    return (completed_at - started_at).total_seconds()


def comic_target_labels(db: Session, jobs: list[ScanJob]) -> dict[int, str]:
    """
    Display titles for jobs that target a comic, keyed by job id.
    Loads every title in one query; falls back to the title saved in the
    job summary when the comic has since been deleted.
    """
    comic_jobs = [job for job in jobs if job.target_type == "comic" and job.target_id is not None]
    if not comic_jobs:
        return {}

    comic_ids = {job.target_id for job in comic_jobs}
    rows = (
        db.query(Comic.id, Comic.number, Series.name)
        .join(Volume, Comic.volume_id == Volume.id)
        .join(Series, Volume.series_id == Series.id)
        .filter(Comic.id.in_(comic_ids))
        .all()
    )
    titles = {comic_id: issue_display_title(series_name, number) for comic_id, number, series_name in rows}

    labels = {}
    for job in comic_jobs:
        summary = json.loads(job.result_summary) if job.result_summary else {}
        labels[job.id] = titles.get(job.target_id) or summary.get("issue_title") or "Deleted Comic"
    return labels


def serialize_job(job: ScanJob, queue_position: int | None = None, target_label: str | None = None) -> dict:
    return {
        "id": job.id,
        "library_id": job.library_id,
        "library_name": determine_library_name(job.job_type, job.library),
        "job_type": job_type_value(job.job_type),
        "lane": JOB_TYPE_LANES.get(job_type_value(job.job_type), LIBRARY_LANE),
        "target_type": job.target_type,
        "target_id": job.target_id,
        "target_label": target_label,
        "status": status_value(job.status),
        "force_scan": bool(job.force_scan),
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "duration_seconds": duration_seconds(job.started_at, job.completed_at),
        "queue_position": queue_position,
        "summary": json.loads(job.result_summary) if job.result_summary else None,
        "error": job.error_message,
    }


def job_type_priority_expression():
    return case(
        *[(ScanJob.job_type == job_type, priority) for job_type, priority in JOB_TYPE_PRIORITY.items()],
        else_=99,
    )


@router.get("/active", name="active")
async def get_active_job(db: SessionDep):
    """
    Get the currently running scan job.
    OPTIMIZED: Eager loads the Library to avoid a secondary DB query.
    """
    # joinedload(ScanJob.library) ensures determine_library_name doesn't hit the DB
    # Only library-lane jobs drive the "scanning" indicator; enrichment jobs
    # such as review lookups run alongside them and are not scans.
    job = db.query(ScanJob).options(joinedload(ScanJob.library)).filter(
        ScanJob.status == JobStatus.RUNNING,
        library_lane_filter(),
    ).order_by(asc(ScanJob.started_at), asc(ScanJob.created_at), asc(ScanJob.id)).first()

    if not job:
        return {"active": False}

    return {
        "active": True,
        "job_id": job.id,
        "library_id": job.library_id,
        "library_name": determine_library_name(job.job_type, job.library),
        "job_type": job_type_value(job.job_type),
        "started_at": job.started_at,
        "force_scan": job.force_scan,
    }


@router.get("", name="list", tags=["admin"])
async def list_jobs(
    admin_user: AdminUser,
    db: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    status: Annotated[Optional[str], Query(pattern="^(pending|running|completed|failed)$")] = None,
    include_enrichment: bool = False,
):
    """
    List recent jobs.
    Enrichment jobs (e.g. review lookups) are frequent, so they are left out
    unless include_enrichment is set.
    OPTIMIZED: Uses joinedload to fetch Job + Library in 1 query.
    """
    # Start query with Eager Loading
    query = db.query(ScanJob).options(joinedload(ScanJob.library))

    if status:
        query = query.filter(ScanJob.status == status)

    if not include_enrichment:
        query = query.filter(library_lane_filter())

    # Order by newest first
    jobs = query.order_by(desc(ScanJob.created_at)).limit(limit).all()
    labels = comic_target_labels(db, jobs)

    return [serialize_job(job, target_label=labels.get(job.id)) for job in jobs]


@router.get("/queue", name="queue", tags=["admin"])
async def get_job_queue(admin_user: AdminUser, db: SessionDep):
    """
    List jobs that are currently running or waiting for the background workers.
    Pending jobs are sorted in the same priority order used by ScanManager.
    Each lane has its own worker, so queue positions are numbered per lane.
    """
    priority_order = job_type_priority_expression()

    running_jobs = (
        db.query(ScanJob)
        .options(joinedload(ScanJob.library))
        .filter(ScanJob.status == JobStatus.RUNNING)
        .order_by(asc(ScanJob.started_at), asc(ScanJob.created_at), asc(ScanJob.id))
        .all()
    )
    pending_jobs = (
        db.query(ScanJob)
        .options(joinedload(ScanJob.library))
        .filter(ScanJob.status == JobStatus.PENDING)
        .order_by(priority_order, asc(ScanJob.created_at), asc(ScanJob.id))
        .all()
    )

    labels = comic_target_labels(db, running_jobs + pending_jobs)

    running_results = [serialize_job(job, target_label=labels.get(job.id)) for job in running_jobs]
    pending_results = []
    lane_positions: dict[str, int] = {}
    for job in pending_jobs:
        lane = JOB_TYPE_LANES.get(job_type_value(job.job_type), LIBRARY_LANE)
        lane_positions[lane] = lane_positions.get(lane, 0) + 1
        pending_results.append(
            serialize_job(job, queue_position=lane_positions[lane], target_label=labels.get(job.id))
        )

    return {
        "active": bool(running_results or pending_results),
        "generated_at": datetime.now(timezone.utc),
        "counts": {
            "running": len(running_results),
            "pending": len(pending_results),
        },
        "running": running_results,
        "pending": pending_results,
        "jobs": running_results + pending_results,
    }


@router.get("/status/{job_id}", name="status")
async def get_job_status(
    job_id: int,
    db: SessionDep,
    user: AdminUser
):
    """
    Get the live status of a specific job.
    """
    # Replaced db.get() with query().options() to ensure no lazy load on library access
    job = db.query(ScanJob).options(joinedload(ScanJob.library)).filter(ScanJob.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return job

@router.get("/{job_id}", name="detail", tags=["admin"])
async def get_job_details(job_id: int, db: SessionDep, admin_user: AdminUser):
    """
    Get detailed status of a specific job.
    OPTIMIZED: Eager loads library.
    """
    job = db.query(ScanJob).options(joinedload(ScanJob.library)).filter(ScanJob.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")


    return {
        "id": job.id,
        "library_id": job.library_id,
        "library_name": determine_library_name(job.job_type, job.library),
        "status": job.status,
        "force_scan": job.force_scan,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        # Parse the JSON string
        "summary": json.loads(job.result_summary) if job.result_summary else None,
        "error": job.error_message
    }
