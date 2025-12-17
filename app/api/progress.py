from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Annotated
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import joinedload

from app.core.settings_loader import get_cached_setting
from app.api.deps import SessionDep, CurrentUser
from app.models.reading_progress import ReadingProgress
# Need to import these for joinedload to work
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.services.reading_progress import ReadingProgressService
from app.core.comic_helpers import get_series_age_restriction

router = APIRouter()


class UpdateProgressRequest(BaseModel):
    current_page: int
    total_pages: Optional[int] = None

# Helper to initialize service with the CORRECT user
def get_progress_service(
        db: SessionDep,
        user: CurrentUser,
) -> ReadingProgressService:
    return ReadingProgressService(db, user_id=user.id)


@router.get("/on-deck", name="on_deck_progress")
async def get_on_deck_progress(
        service: Annotated[ReadingProgressService, Depends(get_progress_service)],
        limit: int = 10

):
    """
    Get 'On Deck' items (In Progress), filtering out stale items based on settings.
    OPTIMIZED: Uses joinedload to prevent N+1 queries for Comic/Series data.
    """
    # Get Setting (Default 4 weeks)
    staleness_weeks = get_cached_setting("ui.on_deck.staleness_weeks", default=4)

    # Calculate Cutoff
    cutoff_date = None
    if staleness_weeks > 0:
        cutoff_date = datetime.now(timezone.utc) - timedelta(weeks=staleness_weeks)

    # 3. Query via Service DB directly for optimization
    query = service.db.query(ReadingProgress).options(
        joinedload(ReadingProgress.comic).joinedload(Comic.volume).joinedload(Volume.series)
    ).filter(
        ReadingProgress.user_id == service.user_id,
        ReadingProgress.completed == False,
        ReadingProgress.current_page > 0  # Must have actually started
    )

    # --- AGE RATING FILTER (Poison Pill) ---
    # Hide items from On Deck if the Series is now banned
    series_filter = get_series_age_restriction(service.user)
    if series_filter is not None:
        query = query.filter(series_filter)
    # ---------------------------------------

    if cutoff_date:
        query = query.filter(ReadingProgress.last_read_at >= cutoff_date)

    progress_list = query.order_by(ReadingProgress.last_read_at.desc()).limit(limit).all()

    results = []
    for p in progress_list:
        comic = p.comic
        results.append({
            "comic_id": comic.id,
            "series_name": comic.volume.series.name,
            "number": comic.number,
            "volume_number": comic.volume.volume_number,
            "percentage": p.progress_percentage,
            "thumbnail": f"/api/comics/{comic.id}/thumbnail",
            "last_read": p.last_read_at
        })

    return results

@router.get("/{comic_id}", name="comic_progress")
async def get_comic_progress(comic_id: int,
                             service: Annotated[ReadingProgressService, Depends(get_progress_service)]):
    """Get reading progress for a specific comic"""

    progress = service.get_progress(comic_id)

    if not progress:
        return {"comic_id": comic_id, "has_progress": False}

    return {
        "comic_id": comic_id,
        "has_progress": True,
        "current_page": progress.current_page,
        "total_pages": progress.total_pages,
        "progress_percentage": progress.progress_percentage,
        "pages_remaining": progress.pages_remaining,
        "completed": progress.completed,
        "last_read_at": progress.last_read_at
    }


@router.post("/{comic_id}", name="update")
async def update_comic_progress(
        comic_id: int,
        request: UpdateProgressRequest,
        service: Annotated[ReadingProgressService, Depends(get_progress_service)],
        db: SessionDep
):
    """
    Update reading progress for a comic.
    Transactions are committed here (Controller layer).
    """

    try:
        # 1. Prepare the data (Service performs flush internally)
        progress = service.update_progress(
            comic_id,
            request.current_page,
            request.total_pages
        )

        # 2. Commit the transaction
        db.commit()

        # 3. Refresh to get updated timestamps/IDs from DB
        db.refresh(progress)

        return {
            "comic_id": comic_id,
            "current_page": progress.current_page,
            "total_pages": progress.total_pages,
            "progress_percentage": progress.progress_percentage,
            "pages_remaining": progress.pages_remaining,
            "completed": progress.completed,
            "last_read_at": progress.last_read_at
        }
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{comic_id}/mark-read", name="mark_comic_as_read")
async def mark_comic_as_read(comic_id: int,
                             service: Annotated[ReadingProgressService, Depends(get_progress_service)],
                             db: SessionDep):
    """Mark a comic as completely read"""

    try:
        progress = service.mark_as_read(comic_id)

        # Commit and Refresh
        db.commit()
        db.refresh(progress)

        return {
            "comic_id": comic_id,
            "completed": True,
            "message": "Comic marked as read"
        }
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{comic_id}", name="mark_comic_as_unread")
async def mark_comic_as_unread(comic_id: int,
                               service: Annotated[ReadingProgressService, Depends(get_progress_service)],
                               db: SessionDep):
    """Mark a comic as unread (remove progress)"""


    try:
        service.mark_as_unread(comic_id)

        # Commit the deletion
        db.commit()

        return {
            "comic_id": comic_id,
            "message": "Comic marked as unread"
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/", name="recent_progress")
async def get_recent_progress(
        current_user: CurrentUser,
        service: Annotated[ReadingProgressService, Depends(get_progress_service)],
        filter: Annotated[str, Query(pattern="^(recent|in_progress|completed)$")] = "recent",
        limit: Annotated[int, Query(ge=1, le=100)] = 20

):
    """
    Get reading progress.
    OPTIMIZED: Direct DB queries with eager loading to prevent N+1 loop.
    """

    # We build the query directly to ensure we can attach .options(joinedload...)
    # This bypasses the basic service methods but is necessary for the List View performance.
    # FIX: Explicitly join Comic/Volume/Series so we can filter by Age Rating
    query = service.db.query(ReadingProgress) \
        .join(Comic).join(Volume).join(Series) \
        .options(joinedload(ReadingProgress.comic).joinedload(Comic.volume).joinedload(Volume.series)) \
        .filter(ReadingProgress.user_id == service.user_id)

    # --- AGE RATING FILTER (Poison Pill) ---
    # Hide items from History if the Series is now banned
    series_filter = get_series_age_restriction(current_user)
    if series_filter is not None:
        query = query.filter(series_filter)
    # ---------------------------------------

    if filter == "in_progress":
        query = query.filter(ReadingProgress.completed == False, ReadingProgress.current_page > 0)
    elif filter == "completed":
        query = query.filter(ReadingProgress.completed == True)
    # else: "recent" implies all/any status, just sorted by time

    progress_list = query.order_by(ReadingProgress.last_read_at.desc()).limit(limit).all()

    results = []
    for progress in progress_list:
        comic = progress.comic
        results.append({
            "comic_id": comic.id,
            "series_id": comic.volume.series_id,
            "series": comic.volume.series.name,
            # Handle potential None values safely
            "volume": comic.volume.volume_number if comic.volume else 0,
            "number": comic.number,
            "title": comic.title,
            "filename": comic.filename,
            "thumbnail_path": f"/api/comics/{comic.id}/thumbnail",
            "current_page": progress.current_page,
            "total_pages": progress.total_pages,
            "progress_percentage": progress.progress_percentage,
            "completed": progress.completed,
            "last_read_at": progress.last_read_at
        })

    return {
        "filter": filter,
        "total": len(results),
        "results": results
    }



