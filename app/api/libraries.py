from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Annotated

from app.api.deps import get_db
from app.models.library import Library
from app.models.series import Series
from app.services.scan_manager import scan_manager
from app.api.deps import PaginationParams, PaginatedResponse, SessionDep, CurrentUser, AdminUser

router = APIRouter()


@router.get("/")
async def list_libraries(db: SessionDep,
                         current_user: CurrentUser):
    """List all libraries"""
    libraries = db.query(Library).all()
    return libraries


@router.get("/{library_id}")
async def get_library(library_id: int,
                      db: SessionDep,
                      current_user: CurrentUser):

    """Get a specific library"""
    library = db.query(Library).filter(Library.id == library_id).first()
    if not library:
        raise HTTPException(status_code=404, detail="Library not found")
    return library


@router.get("/{library_id}/series", response_model=PaginatedResponse)
async def get_library_series(
        library_id: int,
        params: Annotated[PaginationParams, Depends()],
        db: SessionDep,
        current_user: CurrentUser
):
    query = db.query(Series).filter(Series.library_id == library_id)
    total = query.count()
    series_list = query.order_by(Series.name).offset(params.skip).limit(params.size).all()

    return {
        "total": total,
        "page": params.page,
        "size": params.size,
        "items": series_list
    }

@router.post("/")
async def create_library(name: str,
                         path: str,
                         db: SessionDep,
                         admin_user: AdminUser):
    """Create a new library"""
    library = Library(name=name, path=path)
    db.add(library)
    db.commit()
    db.refresh(library)
    return library

@router.delete("/{library_id}")
async def delete_library(library_id: int,
                         db: SessionDep,
                         admin_user: AdminUser):
    """Delete a library"""
    library = db.query(Library).filter(Library.id == library_id).first()
    if not library:
        raise HTTPException(status_code=404, detail="Library not found")

    db.delete(library)
    db.commit()
    return {"message": "Library deleted"}


@router.post("/{library_id}/scan")
async def scan_library(
        library_id: int,
        db: SessionDep,
        force: Annotated[bool, Query(description="Force scan")] = False,
        admin_user: AdminUser = None
):
    """
    Scan a library for comics and import them

    - **force**: If true, scans all files even if they haven't been modified since last scan
    """
    library = db.query(Library).filter(Library.id == library_id).first()
    if not library:
        raise HTTPException(status_code=404, detail="Library not found")

    # Add to manager queue
    result = scan_manager.add_task(library_id, force=force)

    # Returns: {"status": "queued", "job_id": 123, "message": "..."}
    return result

@router.get("/status/scanner")
async def get_scanner_status():
    """Check if a scan is currently running"""
    return scan_manager.get_status()
