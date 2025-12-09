from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Annotated, Optional
from pydantic import BaseModel
from sqlalchemy import func, case

from app.core.comic_helpers import get_smart_cover
from app.models.library import Library
from app.models.series import Series
from app.models.comic import Comic, Volume
from app.models.reading_progress import ReadingProgress
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


@router.get("/", name="list")
async def list_libraries(db: SessionDep, current_user: CurrentUser):
    """List libraries accessible to the current user"""

    # Fetch the libraries based on permissions
    if current_user.is_superuser:
        libs = db.query(Library).all()
    else:
        libs = current_user.accessible_libraries

    # Iterate and Count
    results = []
    for lib in libs:

        # Count Series directly
        series_count = db.query(Series).filter(Series.library_id == lib.id).count()

        # Count Issues (Join Comic -> Volume -> Series)
        issue_count = db.query(Comic).join(Volume).join(Series) \
            .filter(Series.library_id == lib.id).count()

        # Construct the response dict
        # We manually build the dict to inject the 'stats' object
        results.append({
            "id": lib.id,
            "name": lib.name,
            "path": lib.path,
            "watch_mode": lib.watch_mode,
            "created_at": lib.created_at,
            "stats": {
                "series": series_count,
                "issues": issue_count
            }
        })

    return results


@router.get("/{library_id}", name="detail")
async def get_library(library: LibraryDep):

    return library


@router.get("/{library_id}/series", response_model=PaginatedResponse, name="series")
async def get_library_series(
        library: LibraryDep,
        params: Annotated[PaginationParams, Depends()],
        db: SessionDep,
        current_user: CurrentUser,
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

        # OPTIMIZED READ CHECK (Single Query)
        # We count total comics AND read comics in one DB trip using conditional aggregation.
        # This is significantly faster on low-end hardware than running two separate count() queries.
        counts = db.query(
            func.count(Comic.id).label('total'),
            func.count(case((ReadingProgress.completed == True, 1))).label('read')
        ).select_from(Comic).outerjoin(
            ReadingProgress,
            (ReadingProgress.comic_id == Comic.id) & (ReadingProgress.user_id == current_user.id)
        ).join(Volume).filter(Volume.series_id == s.id).first()

        # Logic: It is "Read" only if you own items AND you have read all of them.
        is_fully_read = (counts.total > 0) and (counts.read >= counts.total)

        items.append({
            "id": s.id,
            "name": s.name,
            "library_id": s.library_id,
            "start_year": first_issue.year,
            # Use getattr to be safe if you haven't migrated DB for timestamps yet
            "created_at": getattr(s, 'created_at', None),
            "thumbnail_path": f"/api/comics/{first_issue.id}/thumbnail" if first_issue else None,
            "read": is_fully_read,
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

@router.post("/", tags=["admin"], name="create")
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

@router.patch("/{library_id}", tags=["admin"], name="update")
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

@router.delete("/{library_id}", tags=["admin"], name="delete")
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


@router.post("/{library_id}/scan", tags=["admin"], name="scan")
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

@router.get("/status/scanner", name="scan_status")
async def get_scanner_status():
    """Check if a scan is currently running"""
    return scan_manager.get_status()
