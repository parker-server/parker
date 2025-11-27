from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional, Annotated

from app.api.deps import SessionDep, CurrentUser
from app.models.reading_progress import ReadingProgress
from app.services.reading_progress import ReadingProgressService

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

@router.get("/{comic_id}")
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


@router.post("/{comic_id}")
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


@router.post("/{comic_id}/mark-read")
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


@router.delete("/{comic_id}")
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


@router.get("/")
async def get_recent_progress(
        service: Annotated[ReadingProgressService, Depends(get_progress_service)],
        filter: Annotated[str, Query(pattern="^(recent|in_progress|completed)$")] = "recent",
        limit: Annotated[int, Query(ge=1, le=100)] = 20

):
    """
    Get reading progress.
    Read-only operation, so no commits needed.
    """


    if filter == "in_progress":
        progress_list = service.get_in_progress(limit)
    elif filter == "completed":
        progress_list = service.get_completed(limit)
    else:  # recent
        progress_list = service.get_recently_read(limit)

    results = []
    for progress in progress_list:
        comic = progress.comic
        results.append({
            "comic_id": comic.id,
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