from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Annotated, Optional
from pydantic import BaseModel
from sqlalchemy import func, case

from app.core.comic_helpers import get_smart_cover
from app.models.library import Library
from app.models.series import Series
from app.models.comic import Comic, Volume
from app.services.scan_manager import scan_manager
from app.services.watcher import library_watcher
from app.api.deps import PaginationParams, PaginatedResponse, SessionDep, CurrentUser, AdminUser, LibraryDep

router = APIRouter()

def _has_library_access(library_id: int, current_user: CurrentUser) -> bool:

    if current_user.is_superuser:
        return True

    allowed_ids = [lib.id for lib in current_user.accessible_libraries]
    if library_id not in allowed_ids:
        return False

    return True


@router.get("/")
async def list_libraries(db: SessionDep, current_user: CurrentUser):
    """List libraries accessible to the current user"""

    # Superusers see everything
    if current_user.is_superuser:
        return db.query(Library).all()

    # Regular users see only assigned libraries
    return current_user.accessible_libraries


@router.get("/{library_id}")
async def get_library(library: LibraryDep):

    return library


@router.get("/{library_id}/series", response_model=PaginatedResponse)
async def get_library_series(
        library: LibraryDep,
        params: Annotated[PaginationParams, Depends()],
        db: SessionDep
):
    """
    Get all Series within a specific Library (Paginated).
    Sorts alphabetically ignoring 'The ' prefix.
    """

    # 1. Filter by Library
    query = db.query(Series).filter(Series.library_id == library.id)

    # 2. Pagination
    total = query.count()

    # SMART SORTING: Ignore "The " prefix
    # Logic: If name starts with "The ", use substring starting at char 5. Else use name.
    # We use .ilike for case-insensitive matching
    sort_key = case(
        (Series.name.ilike("The %"), func.substr(Series.name, 5)),
        else_=Series.name
    )


    #series_list = query.order_by(Series.name).offset(params.skip).limit(params.size).all()
    series_list = query.order_by(sort_key).offset(params.skip).limit(params.size).all()

    # 3. Serialization & Thumbnails
    items = []
    for s in series_list:
        # Find a cover (First issue of first volume)
        base_query = db.query(Comic).join(Volume).filter(Volume.series_id == s.id)
        first_issue = get_smart_cover(base_query)

        items.append({
            "id": s.id,
            "name": s.name,
            "library_id": s.library_id,
            "start_year": first_issue.year,
            # Use getattr to be safe if you haven't migrated DB for timestamps yet
            "created_at": getattr(s, 'created_at', None),
            "thumbnail_path": f"/api/comics/{first_issue.id}/thumbnail" if first_issue else None
        })

    return {
        "total": total,
        "page": params.page,
        "size": params.size,
        "items": items
    }

class LibraryCreate(BaseModel):
    name: str
    path: str
    watch_mode: bool = False

@router.post("/")
async def create_library(lib_in: LibraryCreate,
                         db: SessionDep,
                         admin_user: AdminUser):
    """Create a new library"""

    # Check for duplicates
    existing = db.query(Library).filter(Library.name == lib_in.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Library name already exists")

    library = Library(name=lib_in.name, path=lib_in.path)

    db.add(library)
    db.commit()
    db.refresh(library)

    # Notify Watcher
    if library.watch_mode:
        library_watcher.refresh_watches()

    return library

# Schema for updates
class LibraryUpdate(BaseModel):
    name: Optional[str] = None
    path: Optional[str] = None
    watch_mode: Optional[bool] = None

@router.patch("/{library_id}")
async def update_library(
        library_id: int,
        updates: LibraryUpdate,
        db: SessionDep,
        admin_user: AdminUser  # Admin only
):
    """Update library details (Name or Path)"""
    library = db.query(Library).filter(Library.id == library_id).first()
    if not library:
        raise HTTPException(status_code=404, detail="Library not found")

    if updates.name:
        # Check for duplicate names
        existing = db.query(Library).filter(Library.name == updates.name).filter(Library.id != library_id).first()
        if existing:
            raise HTTPException(status_code=400, detail="Library name already exists")
        library.name = updates.name

    if updates.path:
        library.path = updates.path
        # Note: Changing path doesn't delete existing comics, but the next scan
        # might mark them as 'missing' if the new path is totally different.

    if updates.watch_mode is not None:
        library.watch_mode = updates.watch_mode

    db.commit()
    db.refresh(library)

    # Notify Watcher (Always refresh, covering both Enable and Disable cases)
    library_watcher.refresh_watches()

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
