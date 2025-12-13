from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Annotated, Optional
from pydantic import BaseModel
from sqlalchemy import func, case

from app.core.comic_helpers import get_smart_cover, NON_PLAIN_FORMATS, get_series_age_restriction
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
async def list_libraries(db: SessionDep,
                         current_user: CurrentUser,
                         limit: Optional[int] = Query(None, gt=0, description="Limit the number of results")):
    """List libraries accessible to the current user"""

    # Fetch the libraries based on permissions
    if current_user.is_superuser:
        query = db.query(Library).order_by(Library.name)
        if limit:
            query = query.limit(limit)
        libs = query.all()
    else:
        libs = sorted(current_user.accessible_libraries, key=lambda lib: lib.name)
        if limit:
            libs = libs[:limit]

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
            "scan_on_startup": lib.scan_on_startup,
            "watch_mode": lib.watch_mode,
            "last_scanned": lib.last_scanned,
            "is_scanning": lib.is_scanning,
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
    Optimized to avoid N+1 queries by pre-fetching comic data in batch.
    """

    # 1. Filter by Library
    query = db.query(Series).filter(Series.library_id == library.id)

    # --- AGE RATING FILTER ---
    age_filter = get_series_age_restriction(current_user)
    if age_filter is not None:
        query = query.filter(age_filter)
    # -------------------------


    # 2. Pagination
    total = query.count()

    # SMART SORTING: Ignore "The " prefix
    # Logic: If name starts with "The ", use substring starting at char 5. Else use name.
    # We use .ilike for case-insensitive matching
    sort_key = case(
        (Series.name.ilike("The %"), func.substr(Series.name, 5)),
        else_=Series.name
    )

    series_list = query.order_by(sort_key).offset(params.skip).limit(params.size).all()
    if not series_list:
        return {"total": total, "page": params.page, "size": params.size, "items": []}

    # --- BATCH OPTIMIZATION START ---

    # A. Collect Series IDs for this page
    series_ids = [s.id for s in series_list]

    # B. Fetch ALL Comics for these series in one go (Lightweight columns only)
    # We need: id, number, year (for cover selection) and volume.series_id (for grouping)
    # This replaces the 50 'get_smart_cover' queries + 50 'count' queries
    raw_comics = (
        db.query(
            Comic.id,
            Comic.number,
            Comic.year,
            Comic.format,
            Volume.series_id,
            Volume.volume_number
        )
        .join(Volume)
        .filter(Volume.series_id.in_(series_ids))
        .all()
    )

    # C. Fetch Progress for these comics in one go
    # Only fetch 'completed' entries for the current user
    comic_ids = [c.id for c in raw_comics]
    read_comic_ids = set()
    if comic_ids:
        progress_rows = (
            db.query(ReadingProgress.comic_id)
            .filter(
                ReadingProgress.user_id == current_user.id,
                ReadingProgress.completed == True,
                ReadingProgress.comic_id.in_(comic_ids)
            )
            .all()
        )
        read_comic_ids = {r.comic_id for r in progress_rows}

    # D. Group Comics by Series in Python
    from collections import defaultdict
    series_map = defaultdict(list)
    for row in raw_comics:
        series_map[row.series_id].append(row)

    # --- BATCH OPTIMIZATION END ---

    # Helper: Check if format is "Standard" (Not Annual/Special)
    def is_standard_format(fmt: str) -> bool:
        if not fmt: return True
        f = fmt.lower()
        return f not in NON_PLAIN_FORMATS

    # Helper: Safe Sort Key for issues
    def issue_sort_key(c):
        try:
            return float(c.number)
        except:
            return 999999

    # 3. Serialization & Thumbnails
    items = []
    for s in series_list:

        s_comics = series_map.get(s.id, [])

        # Logic: Calculate Read Status
        total_count = len(s_comics)
        read_count = sum(1 for c in s_comics if c.id in read_comic_ids)
        is_fully_read = (total_count > 0) and (read_count >= total_count)

        # Find a cover (First issue of first volume)
        # Logic: Find Smart Cover (Python version of get_smart_cover)
        # 1. Look for Issue "1"
        # 2. Else look for lowest number
        # 3. Else first found

        cover_comic = None
        if s_comics:

            # Filter for standards
            standards = [c for c in s_comics if is_standard_format(c.format)]

            # Decide which pool to search (Prefer standards, fallback to all)
            pool = standards if standards else s_comics

            # Try finding issue #1 exactly in the pool
            issue_ones = [c for c in pool if c.number == '1']

            if issue_ones:
                issue_ones.sort(key=lambda c: c.volume_number)
                cover_comic = issue_ones[0]
            else:
                # Fallback: Sort by number
                pool.sort(key=issue_sort_key)
                cover_comic = pool[0]

        items.append({
            "id": s.id,
            "name": s.name,
            "library_id": s.library_id,
            "start_year": cover_comic.year if cover_comic else None,
            "created_at": getattr(s, 'created_at', None),
            "thumbnail_path": f"/api/comics/{cover_comic.id}/thumbnail" if cover_comic else None,
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

