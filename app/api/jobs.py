from fastapi import APIRouter, Depends, HTTPException, Query

from sqlalchemy import desc
from typing import List, Optional, Annotated
import json

from app.api.deps import get_db, SessionDep, AdminUser
from app.models.job import ScanJob, JobStatus
from app.models.library import Library

router = APIRouter()

@router.get("/active")
async def get_active_job(db: SessionDep):
    """
    Get the currently running scan job (if any).
    Useful for showing a global progress spinner.
    """
    job = db.query(ScanJob).filter(
        ScanJob.status == JobStatus.RUNNING
    ).first()

    if not job:
        return {"active": False}

    return {
        "active": True,
        "job_id": job.id,
        "library_id": job.library_id,
        "library_name": job.library.name if job.library else "Unknown",
        "started_at": job.started_at,
        "force_scan": job.force_scan
    }


@router.get("")
async def list_jobs(
    admin_user: AdminUser,
    db: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    status: Annotated[Optional[str], Query(pattern="^(pending|running|completed|failed)$")] = None

):
    """
    List recent scan jobs.
    """
    query = db.query(ScanJob)

    if status:
        query = query.filter(ScanJob.status == status)

    # Order by newest first
    jobs = query.order_by(desc(ScanJob.created_at)).limit(limit).all()

    results = []
    for job in jobs:
        results.append({
            "id": job.id,
            "library_name": job.library.name if job.library else "Deleted Library",
            "status": job.status,
            "created_at": job.created_at,
            "completed_at": job.completed_at,
            "duration_seconds": (job.completed_at - job.started_at).total_seconds() if job.completed_at and job.started_at else None,
            # Parse the JSON string stored in DB so it returns as a real object
            "summary": json.loads(job.result_summary) if job.result_summary else None,
            "error": job.error_message
        })

    return results


@router.get("/{job_id}")
async def get_job_details(job_id: int, db: SessionDep, admin_user: AdminUser):
    """
    Get detailed status of a specific job.
    Frontend should poll this endpoint every few seconds while status='running'.
    """
    job = db.query(ScanJob).filter(ScanJob.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "id": job.id,
        "library_id": job.library_id,
        "library_name": job.library.name if job.library else "Deleted Library",
        "status": job.status,
        "force_scan": job.force_scan,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        # Parse the JSON string
        "summary": json.loads(job.result_summary) if job.result_summary else None,
        "error": job.error_message
    }