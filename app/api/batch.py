from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone

from app.api.deps import SessionDep, CurrentUser
from app.models.comic import Comic, Volume
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
    OPTIMIZED: Uses bulk operations (Insert/Update mappings) to handle large sets efficiently.
    """
    target_comic_ids = set(payload.comic_ids)

    # 1. Expand Volumes (Future proofing)
    if payload.volume_ids:
        # Find all comics in these volumes
        vol_comics = db.query(Comic.id).filter(Comic.volume_id.in_(payload.volume_ids)).all()
        target_comic_ids.update(c[0] for c in vol_comics)

    # 2. Expand Series (Future proofing)
    if payload.series_ids:
        # Find all comics in these series
        series_comics = db.query(Comic.id).join(Volume).filter(Volume.series_id.in_(payload.series_ids)).all()
        target_comic_ids.update(c[0] for c in series_comics)

    if not target_comic_ids:
        return {"message": "No items selected"}

    # 3. Perform the Batch Update, Logic Fork: Mark Read vs Unread
    # We use an "Upsert" logic or simple check-then-update
    if payload.read:
        # --- MARK READ OPTIMIZATION ---

        # A. Find Existing Progress records (We need the IDs for bulk updates)
        existing_records = db.query(ReadingProgress.id, ReadingProgress.comic_id).filter(
            ReadingProgress.user_id == current_user.id,
            ReadingProgress.comic_id.in_(target_comic_ids)
        ).all()

        # Map comic_id -> progress_id
        existing_map = {r.comic_id: r.id for r in existing_records}

        # B. Fetch Page Counts for ALL targets (Required to set 'current_page' correctly)
        # This is 1 fast query returning tuples
        comics = db.query(Comic.id, Comic.page_count).filter(Comic.id.in_(target_comic_ids)).all()
        comic_page_map = {c.id: (c.page_count or 0) for c in comics}

        # C. Prepare Bulk Data
        inserts = []
        updates = []
        now = datetime.now(timezone.utc)

        for comic_id in target_comic_ids:
            # Integrity check: Ensure comic actually exists
            if comic_id not in comic_page_map:
                continue

            total_pages = comic_page_map[comic_id]
            # Set to last page (0-indexed)
            final_page = max(0, total_pages - 1) if total_pages > 0 else 0

            if comic_id in existing_map:
                # Update existing record
                updates.append({
                    "id": existing_map[comic_id],  # PK is required for bulk_update
                    "completed": True,
                    "current_page": final_page,
                    "total_pages": total_pages,
                    "last_read_at": now
                })
            else:
                # Insert new record
                inserts.append({
                    "user_id": current_user.id,
                    "comic_id": comic_id,
                    "completed": True,
                    "current_page": final_page,
                    "total_pages": total_pages,
                    "last_read_at": now
                })

        # D. Execute Bulk Operations
        if inserts:
            db.bulk_insert_mappings(ReadingProgress, inserts)
        if updates:
            db.bulk_update_mappings(ReadingProgress, updates)

        action_msg = "read"

    else:
        # --- MARK UNREAD LOGIC ---
        # Direct SQL Delete is already optimal
        db.query(ReadingProgress).filter(
            ReadingProgress.user_id == current_user.id,
            ReadingProgress.comic_id.in_(target_comic_ids)
        ).delete(synchronize_session=False)

        action_msg = "unread"

    db.commit()

    return {"message": f"Marked {len(target_comic_ids)} comics as {action_msg}"}