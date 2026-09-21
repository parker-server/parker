import json
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import asc, case, desc
from sqlalchemy.orm import joinedload

from app.api.deps import SessionDep, AdminUser
from app.models.job import ScanJob, JobStatus, JobType
from app.models.library import Library

router = APIRouter()

JOB_TYPE_PRIORITY = {
    JobType.SCAN.value: 0,
    JobType.THUMBNAIL.value: 1,
    JobType.CLEANUP.value: 2,
    JobType.METADATA_REHYDRATE.value: 3,
}


def job_type_value(job_type: JobType | str | None) -> str:
    if isinstance(job_type, JobType):
        return job_type.value
    return job_type or JobType.SCAN.value


def status_value(status: JobStatus | str | None) -> str:
    if isinstance(status, JobStatus):
        return status.value
    return status or JobStatus.PENDING.value


def determine_library_name(job_type: JobType | str | None, job_library: Library | None) -> str:
    if job_type_value(job_type) == JobType.CLEANUP.value and not job_library:
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


def serialize_job(job: ScanJob, queue_position: int | None = None) -> dict:
    return {
        "id": job.id,
        "library_id": job.library_id,
        "library_name": determine_library_name(job.job_type, job.library),
        "job_type": job_type_value(job.job_type),
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
        (ScanJob.job_type == JobType.SCAN.value, JOB_TYPE_PRIORITY[JobType.SCAN.value]),
        (ScanJob.job_type == JobType.THUMBNAIL.value, JOB_TYPE_PRIORITY[JobType.THUMBNAIL.value]),
        (ScanJob.job_type == JobType.CLEANUP.value, JOB_TYPE_PRIORITY[JobType.CLEANUP.value]),
        (
            ScanJob.job_type == JobType.METADATA_REHYDRATE.value,
            JOB_TYPE_PRIORITY[JobType.METADATA_REHYDRATE.value],
        ),
        else_=99,
    )


@router.get("/active", name="active")
async def get_active_job(db: SessionDep):
    """
    Get the currently running scan job.
    OPTIMIZED: Eager loads the Library to avoid a secondary DB query.
    """
    # joinedload(ScanJob.library) ensures determine_library_name doesn't hit the DB
    job = db.query(ScanJob).options(joinedload(ScanJob.library)).filter(
        ScanJob.status == JobStatus.RUNNING
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
    status: Annotated[Optional[str], Query(pattern="^(pending|running|completed|failed)$")] = None

):
    """
    List recent scan jobs.
    OPTIMIZED: Uses joinedload to fetch Job + Library in 1 query.
    """
    # Start query with Eager Loading
    query = db.query(ScanJob).options(joinedload(ScanJob.library))

    if status:
        query = query.filter(ScanJob.status == status)

    # Order by newest first
    jobs = query.order_by(desc(ScanJob.created_at)).limit(limit).all()

    return [serialize_job(job) for job in jobs]


@router.get("/queue", name="queue", tags=["admin"])
async def get_job_queue(admin_user: AdminUser, db: SessionDep):
    """
    List jobs that are currently running or waiting for the background worker.
    Pending jobs are sorted in the same priority order used by ScanManager.
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

    running_results = [serialize_job(job) for job in running_jobs]
    pending_results = [
        serialize_job(job, queue_position=index)
        for index, job in enumerate(pending_jobs, start=1)
    ]

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
