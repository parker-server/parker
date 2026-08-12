from fastapi import APIRouter, Depends, HTTPException, Query

from sqlalchemy import desc
from sqlalchemy.orm import joinedload
from typing import List, Optional, Annotated
import json

from app.api.deps import get_db, SessionDep, AdminUser
from app.models.job import ScanJob, JobStatus, JobType
from app.models.library import Library

router = APIRouter()

def determine_library_name(job_type: JobType, job_library: Library) -> str:
    if job_type == JobType.CLEANUP and not job_library:
        library_name = "-"
    elif not job_library:
        library_name = "Deleted Library"
    else:
        library_name = job_library.name

    return library_name

@router.get("/active", name="active")
async def get_active_job(db: SessionDep):
    """
    Get the currently running scan job.
    OPTIMIZED: Eager loads the Library to avoid a secondary DB query.
    """
    # joinedload(ScanJob.library) ensures determine_library_name doesn't hit the DB
    job = db.query(ScanJob).options(joinedload(ScanJob.library)).filter(
        ScanJob.status == JobStatus.RUNNING
    ).first()

    if not job:
        return {"active": False}

    return {
        "active": True,
        "job_id": job.id,
        "library_id": job.library_id,
        "library_name": determine_library_name(job.job_type, job.library),
        "started_at": job.started_at,
        "force_scan": job.force_scan
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

    results = []
    for job in jobs:
        results.append({
            "id": job.id,
            "library_name": determine_library_name(job.job_type, job.library),
            "job_type": job.job_type,
            "status": job.status,
            "created_at": job.created_at,
            "completed_at": job.completed_at,
            "duration_seconds": (job.completed_at - job.started_at).total_seconds() if job.completed_at and job.started_at else None,
            # Parse the JSON string stored in DB so it returns as a real object
            "summary": json.loads(job.result_summary) if job.result_summary else None,
            "error": job.error_message
        })

    return results

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