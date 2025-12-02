from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, FileResponse
from sqlalchemy import func, Float
from typing import List, Annotated, Optional, Literal
from pathlib import Path
import re




from app.api.deps import SessionDep, CurrentUser
from app.models.comic import Comic, Volume
from app.models.series import Series

from app.schemas.search import SearchRequest, SearchResponse
from app.services.search import SearchService
from app.services.images import ImageService
from app.models.reading_progress import ReadingProgress
from app.models.pull_list import PullList, PullListItem
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.collection import Collection, CollectionItem

router = APIRouter()

def natural_sort_key(s):
    """Sorts 'Issue 1' before 'Issue 10' and handles '10a'"""
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split('([0-9]+)', str(s))]

@router.get("/{comic_id}/read-init")
async def get_comic_reader_init(comic_id: int,
                                db: SessionDep,
                                current_user: CurrentUser,
                                # Context Parameters
                                context_type: Annotated[
                                    Optional[Literal["volume", "reading_list", "pull_list", "collection", "series"]],
                                    "Specifies the type of context; defaults to 'volume'"
                                ] = "volume",
                                context_id: Annotated[
                                    Optional[int],
                                    "Unique identifier for the given context"
                                ] = None):
    """
    Get initialization data for the reader:
    - Page Count
    - Previous / Next Comic IDs in the volume
    """
    comic = db.query(Comic).filter(Comic.id == comic_id).first()
    if not comic:
        raise HTTPException(status_code=404, detail="Comic not found")

    # Default: No Context (Standard Volume Browsing)
    prev_id = None
    next_id = None

    # --- STRATEGY PATTERN ---
    if context_type == "pull_list" and context_id:
        # 1. Pull List Strategy
        # Query items in THIS specific list, ordered by sort_order
        items = db.query(PullListItem.comic_id).filter(
            PullListItem.pull_list_id == context_id
        ).order_by(PullListItem.sort_order).all()

        # Flatten tuple list [(1,), (2,)] -> [1, 2]
        ids = [i[0] for i in items]

    elif context_type == "reading_list" and context_id:
        # 2. Reading List Strategy (Fixes Armageddon 2001)
        items = db.query(ReadingListItem.comic_id).filter(
            ReadingListItem.reading_list_id == context_id
        ).order_by(ReadingListItem.position).all()
        ids = [i[0] for i in items]

    elif context_type == "collection" and context_id:
        # 3. Collection Strategy (Thematic)
        # Collections usually don't have explicit order
        # Simplified Sort: Year -> Series -> Number
        items = db.query(CollectionItem.comic_id) \
            .join(Comic, CollectionItem.comic_id == Comic.id) \
            .join(Volume, Comic.volume_id == Volume.id) \
            .join(Series, Volume.series_id == Series.id) \
            .filter(CollectionItem.collection_id == context_id) \
            .order_by(
                Comic.year.asc(),  # 1. Chronological groups
        Series.name.asc(),  # 2. Alphabetical by Title
                func.cast(Comic.number, Float)  # 3. Numerical order (essential if multiple issues of same series exist)
        ).all()

        ids = [i[0] for i in items]

    elif context_type == "series" and context_id:
        # 4. Series Strategy
        # Logic: Flatten ALL volumes in the series.
        # Sort by: Volume Number -> Float(Issue Number)
        # This allows seamless reading from Vol 1 #12 -> Vol 2 #1

        series_comics = db.query(Comic).join(Volume).filter(
            Volume.series_id == context_id
        ).order_by(
            Volume.volume_number,
            func.cast(Comic.number, Float),  # Use Float cast for correct 1, 2, 10 sorting
            Comic.number
        ).all()

        ids = [c.id for c in series_comics]

    else:
        # 5. Default / Volume Strategy
        # Used for context_type="volume" OR fallback
        # Logic: Sort all siblings in THIS volume by natural number
        # Note: This fixes your Annuals issue if we sort correctly here
        # We grab ALL siblings in the volume (Annuals + Plain) sorted by date/number
        siblings = db.query(Comic).filter(
            Comic.volume_id == comic.volume_id
        ).all()

        # Sort using your helper (ensures Annuals slot in correctly if numbered/dated)
        siblings.sort(key=lambda x: natural_sort_key(x.number))
        ids = [c.id for c in siblings]

    # --- CALCULATE NEIGHBORS ---
    try:
        current_idx = ids.index(comic_id)

        if current_idx > 0:
            prev_id = ids[current_idx - 1]

        if current_idx < len(ids) - 1:
            next_id = ids[current_idx + 1]

    except ValueError:
        # Edge case: The comic currently reading isn't actually IN the context list provided
        # Fallback to volume logic or return None
        pass

    # Page Count
    image_service = ImageService()
    page_count = image_service.get_page_count(comic.file_path)


    return {
        "comic_id": comic.id,
        "title": comic.title,
        "series_name": comic.volume.series.name,
        "volume_number": comic.volume.volume_number,
        "number": comic.number,
        "page_count": page_count,
        "next_comic_id": next_id,
        "prev_comic_id": prev_id
    }

@router.get("/{comic_id}/page/{page_index}")
def get_comic_page(
        comic_id: int,
        page_index: int,
        db: SessionDep,
        sharpen: Annotated[bool, Query()] = False,
        grayscale: Annotated[bool, Query()] = False,
):
    """
    Get a specific page image from a comic.  Supports server-side sharpening and grayscale.

    Args:
        comic_id: ID of the comic
        page_index: Zero-based page index (0 = first page/cover)
        db: Database session
        sharpen: If true, sharpen the image
        grayscale: If true, convert the image to grayscale
    """
    comic = db.query(Comic).filter(Comic.id == comic_id).first()

    if not comic:
        raise HTTPException(status_code=404, detail="Comic not found")

    image_service = ImageService()
    image_bytes, is_correct_format = image_service.get_page_image(comic.file_path, page_index, sharpen=sharpen, grayscale=grayscale)

    if not image_bytes:
        raise HTTPException(status_code=404, detail="Page not found")

    # Detect image type from bytes
    # If filtered, it's always JPEG. If raw, detect type.
    if sharpen or grayscale:
        media_type = "image/jpeg"
    elif image_bytes.startswith(b'\xff\xd8\xff'):
        media_type = "image/jpeg"
    elif image_bytes.startswith(b'\x89PNG'):
        media_type = "image/png"
    elif image_bytes.startswith(b'GIF'):
        media_type = "image/gif"
    elif image_bytes.startswith(b'RIFF') and b'WEBP' in image_bytes[:20]:
        media_type = "image/webp"
    else:
        media_type = "image/jpeg"  # Default

    # CACHE LOGIC
    headers = {
        "Content-Disposition": f'inline; filename="page_{page_index}.jpg"'
    }

    if is_correct_format:
        # Success: Cache aggressively
        headers["Cache-Control"] = "public, max-age=31536000"
    else:
        # Fallback triggered: DO NOT CACHE
        # This prevents the raw image from being permanently cached
        # for a URL like "?grayscale=true"
        headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

    return Response(
        content=image_bytes,
        media_type=media_type,
        headers=headers
    )
