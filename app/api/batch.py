from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone

from app.api.deps import SessionDep, CurrentUser
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.reading_progress import ReadingProgress

router = APIRouter()


# Schema for the Composite Payload
class BatchActionRequest(BaseModel):
    comic_ids: List[int] = []
    volume_ids: List[int] = []
    series_ids: List[int] = []
    read: bool = True


@router.post("/read-status", name="mark_read")
async def batch_mark_read(
        payload: BatchActionRequest,
        db: SessionDep,
        current_user: CurrentUser
):
    """
    Marks items as read.
    Smart Logic: If a Series/Volume is passed, it finds all contained comics.
    """
    target_comic_ids = set(payload.comic_ids)

    # 1. Expand Volumes (Future proofing)
    if payload.volume_ids:
        # Find all comics in these volumes
        vol_comics = db.query(Comic.id).filter(Comic.volume_id.in_(payload.volume_ids)).all()
        for c in vol_comics:
            target_comic_ids.add(c.id)

    # 2. Expand Series (Future proofing)
    if payload.series_ids:
        # Find all comics in these series
        series_comics = db.query(Comic.id).join(Volume).filter(Volume.series_id.in_(payload.series_ids)).all()
        for c in series_comics:
            target_comic_ids.add(c.id)

    if not target_comic_ids:
        return {"message": "No items selected"}

    # 3. Perform the Batch Update, Logic Fork: Mark Read vs Unread
    # We use an "Upsert" logic or simple check-then-update
    if payload.read:
        # Bulk fetch existing progress to update
        existing_progress = db.query(ReadingProgress).filter(
            ReadingProgress.user_id == current_user.id,
            ReadingProgress.comic_id.in_(target_comic_ids)
        ).all()

        existing_map = {p.comic_id: p for p in existing_progress}

        # Fetch page counts for accurate "100% complete" status
        comics = db.query(Comic.id, Comic.page_count).filter(Comic.id.in_(target_comic_ids)).all()

        for comic in comics:
            progress = existing_map.get(comic.id)
            if not progress:
                progress = ReadingProgress(user_id=current_user.id, comic_id=comic.id, total_pages=comic.page_count or 0)
                db.add(progress)

            progress.current_page = max(0, (comic.page_count or 0) - 1)  # Set to last page
            progress.completed = True
            progress.last_read_at = datetime.now(timezone.utc)

        action_msg = "read"

    else:
        # --- MARK UNREAD LOGIC (Delete Progress) ---
        # Simply delete the progress rows for these comics
        db.query(ReadingProgress).filter(
            ReadingProgress.user_id == current_user.id,
            ReadingProgress.comic_id.in_(target_comic_ids)
        ).delete(synchronize_session=False)

        action_msg = "unread"
        pass

    db.commit()

    return {"message": f"Marked {len(target_comic_ids)} comics as {action_msg}"}