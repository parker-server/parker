from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Annotated, Optional
from pydantic import BaseModel
from sqlalchemy import func, case
from datetime import datetime, timezone
from pathlib import Path as FsPath
import logging
import os

from app.config import settings
from app.core.comic_helpers import get_thumbnail_url, NON_PLAIN_FORMATS, REVERSE_NUMBERING_SERIES, get_series_age_restriction
from app.models.library import Library
from app.models.library_root import LibraryRoot
from app.models.series import Series
from app.models.comic import Comic, Volume
from app.models.interactions import UserLibraryPin
from app.models.reading_progress import ReadingProgress
from app.services.scan_manager import scan_manager
from app.services.watcher import library_watcher
from app.api.deps import PaginationParams, PaginatedResponse, SessionDep, CurrentUser, AdminUser, LibraryDep

router = APIRouter()
logger = logging.getLogger(__name__)


def _normalize_library_path(path: str) -> str:
    return os.path.normcase(os.path.normpath(path.strip()))


def _paths_overlap(first_path: str, second_path: str) -> bool:
    first = _normalize_library_path(first_path)
    second = _normalize_library_path(second_path)

    try:
        common = os.path.commonpath([first, second])
    except ValueError:
        return False

    return common == first or common == second


def _find_overlapping_library(
        db,
        candidate_path: str,
        *,
        exclude_library_id: Optional[int] = None,
) -> Optional[Library]:
    query = db.query(Library)
    if exclude_library_id is not None:
        query = query.filter(Library.id != exclude_library_id)

    for existing in query.all():
        if _paths_overlap(candidate_path, existing.path):
            return existing

    return None


def _has_library_access(library_id: int, current_user: CurrentUser) -> bool:

    if current_user.is_superuser:
        return True

    allowed_ids = [lib.id for lib in current_user.accessible_libraries]
    if library_id not in allowed_ids:
        return False

    return True


def _library_payload(lib: Library, *, pinned: bool = False, stats: Optional[dict] = None) -> dict:
    payload = {
        "id": lib.id,
        "name": lib.name,
        "path": lib.path,
        "scan_on_startup": lib.scan_on_startup,
        "watch_mode": lib.watch_mode,
        "parse_reading_lists": lib.parse_reading_lists,
        "parse_collections": lib.parse_collections,
        "parse_story_arcs": lib.parse_story_arcs,
        "last_scanned": lib.last_scanned,
        "is_scanning": lib.is_scanning,
        "created_at": lib.created_at,
        "pinned": pinned,
    }

    if stats is not None:
        payload["stats"] = stats

    return payload


def _resolve_library_browser_path(path: Optional[str] = None) -> tuple[FsPath, FsPath]:
    root = settings.comics_path.expanduser().resolve()
    requested = FsPath(path).expanduser() if path else root
    current = requested.resolve()

    try:
        current.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path must be within the configured comics root")

    return root, current


@router.get("/", name="list")
async def list_libraries(db: SessionDep,
                         current_user: CurrentUser,
                         limit: Optional[int] = Query(None, gt=0, description="Limit the number of results")):
    """
    List libraries accessible to the current user
    Fetches all stats in 2 batch queries instead of N+1 loops.
    """

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

    if not libs:
        return []

    lib_ids = [lib.id for lib in libs]
    pinned_ids = {
        row.library_id
        for row in db.query(UserLibraryPin.library_id)
        .filter(
            UserLibraryPin.user_id == current_user.id,
            UserLibraryPin.library_id.in_(lib_ids),
        )
        .all()
    }

    # --- AGE RATING FILTER PREP ---
    # We prepare the filter once to reuse inside the loop
    series_age_filter = get_series_age_restriction(current_user)

    # Batch Fetch Series Counts (Grouped by Library)
    # Query: "SELECT library_id, COUNT(id) FROM series GROUP BY library_id"
    series_q = db.query(Series.library_id, func.count(Series.id)) \
        .filter(Series.library_id.in_(lib_ids))

    if series_age_filter is not None:
        series_q = series_q.filter(series_age_filter)

    series_counts = dict(series_q.group_by(Series.library_id).all())

    # Batch Fetch Issue Counts (Grouped by Library)
    # Query: "SELECT library_id, COUNT(comic.id) FROM comic JOIN vol JOIN series ..."
    issue_q = db.query(Series.library_id, func.count(Comic.id)) \
        .join(Volume, Comic.volume_id == Volume.id) \
        .join(Series, Volume.series_id == Series.id) \
        .filter(Series.library_id.in_(lib_ids))

    if series_age_filter is not None:
        issue_q = issue_q.filter(series_age_filter)

    issue_counts = dict(issue_q.group_by(Series.library_id).all())

    # Iterate and Count
    results = []
    for lib in libs:

        results.append(_library_payload(
            lib,
            pinned=lib.id in pinned_ids,
            stats={
                "series": series_counts.get(lib.id, 0),
                "issues": issue_counts.get(lib.id, 0)
            }
        ))

    return results


@router.get("/browse/paths", tags=["admin"], name="browse")
async def browse_library_paths(admin_user: AdminUser, path: Optional[str] = Query(None)):
    root, current = _resolve_library_browser_path(path)

    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=404, detail="Configured comics root was not found")

    if not current.exists() or not current.is_dir():
        raise HTTPException(status_code=404, detail="Directory was not found")

    entries = []
    try:
        children = sorted(current.iterdir(), key=lambda child: child.name.lower())
    except OSError:
        raise HTTPException(status_code=403, detail="Directory cannot be read")

    for child in children:
        try:
            resolved_child = child.resolve()
            resolved_child.relative_to(root)
        except (OSError, ValueError):
            continue

        if not resolved_child.is_dir():
            continue

        entries.append({
            "name": child.name,
            "path": str(resolved_child),
        })

    parent = None
    if current != root:
        parent_path = current.parent.resolve()
        try:
            parent_path.relative_to(root)
            parent = str(parent_path)
        except ValueError:
            parent = str(root)

    return {
        "root": str(root),
        "current": str(current),
        "parent": parent,
        "entries": entries,
    }


@router.get("/{library_id}", name="detail")
async def get_library(library: LibraryDep, db: SessionDep, current_user: CurrentUser):
    pin = db.query(UserLibraryPin).filter(
        UserLibraryPin.user_id == current_user.id,
        UserLibraryPin.library_id == library.id,
    ).first()

    return _library_payload(library, pinned=pin is not None)


@router.post("/{library_id}/pin", name="pin")
async def pin_library(library: LibraryDep, db: SessionDep, current_user: CurrentUser):
    existing = db.query(UserLibraryPin).filter(
        UserLibraryPin.user_id == current_user.id,
        UserLibraryPin.library_id == library.id,
    ).first()

    if existing is None:
        db.add(UserLibraryPin(
            user_id=current_user.id,
            library_id=library.id,
            pinned_at=datetime.now(timezone.utc),
        ))
        db.commit()

    return {"library_id": library.id, "pinned": True}


@router.delete("/{library_id}/pin", name="unpin")
async def unpin_library(library: LibraryDep, db: SessionDep, current_user: CurrentUser):
    pin = db.query(UserLibraryPin).filter(
        UserLibraryPin.user_id == current_user.id,
        UserLibraryPin.library_id == library.id,
    ).first()

    if pin is not None:
        db.delete(pin)
        db.commit()

    return {"library_id": library.id, "pinned": False}


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
            Comic.updated_at,
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

            # GIMMICK DETECTION
            is_reverse = s.name.lower() in REVERSE_NUMBERING_SERIES

            # Filter for standards
            standards = [c for c in s_comics if is_standard_format(c.format)]

            # Decide which pool to search (Prefer standards, fallback to all)
            pool = standards if standards else s_comics

            # Try finding issue #1 exactly in the pool (Only if NOT reverse)
            # (Because for Countdown, #1 is the END, not the cover)
            issue_ones = []
            if not is_reverse:
                issue_ones = [c for c in pool if c.number == '1']

            if issue_ones:
                issue_ones.sort(key=lambda c: c.volume_number)
                cover_comic = issue_ones[0]
            else:
                # Fallback: Sort by number
                pool.sort(key=issue_sort_key)

                # If Reverse Series (Zero Hour), take the LAST item (Highest Number)
                # If Standard Series, take the FIRST item (Lowest Number)
                if is_reverse:
                    cover_comic = pool[-1]
                else:
                    cover_comic = pool[0]

        items.append({
            "id": s.id,
            "name": s.name,
            "library_id": s.library_id,
            "start_year": cover_comic.year if cover_comic else None,
            "created_at": getattr(s, 'created_at', None),
            "thumbnail_path": get_thumbnail_url(cover_comic.id, cover_comic.updated_at) if cover_comic else None,
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
    parse_reading_lists: bool = True
    parse_collections: bool = True
    parse_story_arcs: bool = True

@router.post("/", tags=["admin"], name="create")
async def create_library(lib_in: LibraryCreate,
                         db: SessionDep,
                         admin_user: AdminUser):
    """Create a new library"""

    # Check for duplicates
    existing = db.query(Library).filter(Library.name == lib_in.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Library name already exists")

    overlapping = _find_overlapping_library(db, lib_in.path)
    if overlapping:
        raise HTTPException(
            status_code=400,
            detail=f"Library path overlaps with existing library '{overlapping.name}'"
        )

    library = Library(
        name=lib_in.name,
        path=lib_in.path,
        watch_mode=lib_in.watch_mode,
        parse_reading_lists=lib_in.parse_reading_lists,
        parse_collections=lib_in.parse_collections,
        parse_story_arcs=lib_in.parse_story_arcs,
    )

    db.add(library)
    db.commit()
    db.refresh(library)

    db.add(LibraryRoot(library_id=library.id, path=library.path, is_active=True))
    db.commit()

    # Notify Watcher
    if library.watch_mode:
        library_watcher.refresh_watches()

    return library

# Schema for updates
class LibraryUpdate(BaseModel):
    name: Optional[str] = None
    path: Optional[str] = None
    watch_mode: Optional[bool] = None
    parse_reading_lists: Optional[bool] = None
    parse_collections: Optional[bool] = None
    parse_story_arcs: Optional[bool] = None

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

    if updates.path and updates.path != library.path:
        raise HTTPException(
            status_code=400,
            detail="Changing a library's path is temporarily disabled. Library relocation is "
                   "being rebuilt to preserve reading progress and other data; support is "
                   "coming in a future release.",
        )

    if updates.watch_mode is not None:
        library.watch_mode = updates.watch_mode

    enable_reading_lists = updates.parse_reading_lists is True and not library.parse_reading_lists
    enable_collections = updates.parse_collections is True and not library.parse_collections
    enable_story_arcs = updates.parse_story_arcs is True and not library.parse_story_arcs

    if updates.parse_reading_lists is not None:
        library.parse_reading_lists = updates.parse_reading_lists

    if updates.parse_collections is not None:
        library.parse_collections = updates.parse_collections

    if updates.parse_story_arcs is not None:
        library.parse_story_arcs = updates.parse_story_arcs

    db.commit()
    db.refresh(library)

    rehydration_result = None
    if enable_reading_lists or enable_collections or enable_story_arcs:
        try:
            rehydration_result = scan_manager.add_metadata_rehydrate_task(library.id)
        except Exception as exc:
            logger.error("Failed to queue metadata rehydrate for library %s: %s", library.id, exc)
            rehydration_result = {"status": "failed", "message": "Failed to queue metadata rehydrate"}

    # Notify Watcher (Always refresh, covering both Enable and Disable cases)
    library_watcher.refresh_watches()

    response = {
        "id": library.id,
        "name": library.name,
        "path": library.path,
        "scan_on_startup": library.scan_on_startup,
        "watch_mode": library.watch_mode,
        "parse_reading_lists": library.parse_reading_lists,
        "parse_collections": library.parse_collections,
        "parse_story_arcs": library.parse_story_arcs,
        "last_scanned": library.last_scanned,
        "is_scanning": library.is_scanning,
        "created_at": library.created_at,
    }

    if rehydration_result is not None:
        response["rehydration"] = rehydration_result

    return response

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

