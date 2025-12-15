from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, FileResponse
from sqlalchemy import func, Float, case, or_, cast
from sqlalchemy.orm import joinedload
from typing import List, Annotated, Optional, Literal
from pathlib import Path
import re

from app.core.comic_helpers import (get_format_sort_index, get_format_weight, get_age_rating_config,
                                    get_comic_age_restriction, get_banned_comic_condition)
from app.core.comic_helpers import get_format_sort_index, get_format_weight, REVERSE_NUMBERING_SERIES
from app.api.deps import SessionDep, CurrentUser
from app.models.comic import Comic, Volume
from app.models.series import Series

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

@router.get("/{comic_id}/read-init", name="init")
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
    Get initialization data for the reader.
    OPTIMIZED: Uses tuple queries for sibling sorting instead of full object fetches.
    SECURED: Prevents reading restricted comics and navigation to restricted neighbors.
    Now handles Reverse Numbering (Countdown) and Date Sorting (Zero Hour).
    """
    # 1. Fetch Comic with Series/Volume loaded (Avoids N+1 later)
    comic = db.query(Comic).options(
        joinedload(Comic.volume).joinedload(Volume.series)
    ).filter(Comic.id == comic_id).first()

    if not comic:
        raise HTTPException(status_code=404, detail="Comic not found")

    # --- SECURITY CHECK (Target Comic) ---
    # 1. RLS: Check Library Access
    if not current_user.is_superuser:
        allowed_libs = [l.id for l in current_user.accessible_libraries]
        if comic.volume.series.library_id not in allowed_libs:
            raise HTTPException(status_code=404, detail="Comic not found")

    # 2. Age Rating: Check Target
    if current_user.max_age_rating:

        is_restricted = False
        allowed, banned = get_age_rating_config(current_user)

        # Explicit Ban
        if comic.age_rating in banned:
            is_restricted = True

        # Unknown Ban
        if not current_user.allow_unknown_age_ratings:
            if not comic.age_rating or comic.age_rating == "" or comic.age_rating.lower() == "unknown":
                is_restricted = True

        if is_restricted:
            raise HTTPException(status_code=403, detail="Content restricted by age rating")
    # -------------------------------------


    # Default: No Context (Standard Volume Browsing)
    prev_id = None
    next_id = None
    ids = []
    context_label = ""

    # --- PREPARE NEIGHBOR FILTER ---
    # We must filter the "Next/Prev" lists so users don't navigate INTO a banned book.
    banned_filter = None
    if current_user.max_age_rating:
        banned_filter = get_banned_comic_condition(current_user)


    # Define robust SQL Sort Keys (Date Priority)
    sort_year = case((or_(Comic.year == None, Comic.year == -1), 9999), else_=Comic.year)
    sort_month = case((or_(Comic.month == None, Comic.month == -1), 99), else_=Comic.month)
    sort_day = case((or_(Comic.day == None, Comic.day == -1), 99), else_=Comic.day)
    sort_number = cast(Comic.number, Float)


    # --- STRATEGY PATTERN ---
    if context_type == "pull_list" and context_id:
        # 1. Pull List Strategy

        context_label = db.query(PullList.name).filter(PullList.id == context_id).scalar()

        # Query items in THIS specific list, ordered by sort_order
        query = (db.query(PullListItem.comic_id)
                 .filter(PullListItem.pull_list_id == context_id))

        if banned_filter is not None:
            query = query.filter(~banned_filter)  # Exclude banned

        items = query.order_by(PullListItem.sort_order).all()

        # Flatten tuple list [(1,), (2,)] -> [1, 2]
        ids = [i[0] for i in items]

    elif context_type == "reading_list" and context_id:
        # 2. Reading List Strategy (Fixes Armageddon 2001)

        context_label = db.query(ReadingList.name).filter(ReadingList.id == context_id).scalar()

        query = db.query(ReadingListItem.comic_id).filter(
            ReadingListItem.reading_list_id == context_id)

        if banned_filter is not None:
            query = query.filter(~banned_filter)

        items = query.order_by(ReadingListItem.position).all()

        ids = [i[0] for i in items]

    elif context_type == "collection" and context_id:
        # 3. Collection Strategy (Thematic)

        context_label = db.query(Collection.name).filter(Collection.id == context_id).scalar()

        # Collections usually don't have explicit order
        # Simplified Sort: Year -> Series -> Number
        query = db.query(CollectionItem.comic_id) \
            .join(Comic, CollectionItem.comic_id == Comic.id) \
            .join(Volume, Comic.volume_id == Volume.id) \
            .join(Series, Volume.series_id == Series.id) \
            .filter(CollectionItem.collection_id == context_id)

        if banned_filter is not None:
            query = query.filter(~banned_filter)

        items = query.order_by(
            Comic.year.asc(),
            Series.name.asc(),
            func.cast(Comic.number, Float)
        ).all()

        ids = [i[0] for i in items]

    elif context_type == "series" and context_id:
        # 4. Series Strategy
        context_label = db.query(Series.name).filter(Series.id == context_id).scalar()

        # Gimmick Detection
        number_direction = sort_number.asc()
        if context_label and context_label.lower() in REVERSE_NUMBERING_SERIES:
            number_direction = sort_number.desc()

        # Use centralized helper
        format_weight = get_format_sort_index()

        query = db.query(Comic.id).join(Volume).filter(Volume.series_id == context_id)

        if banned_filter is not None:
            query = query.filter(~banned_filter)

        # Series strategy is already optimized (fetches IDs only via ORM selection)
        # But let's be explicit to avoid object overhead:
        items = query.order_by(
            Volume.volume_number,
            format_weight,  # Plain(1) -> Annual(2) -> Special(3)
            sort_year.asc(),
            sort_month.asc(),
            sort_day.asc(),
            number_direction
        ).all()
        ids = [i[0] for i in items]

    else:
        # 5. Default / Volume Strategy
        # OPTIMIZATION: Fetch lightweight Tuples instead of full Comic objects.
        # This prevents instantiating 900 objects for large series.

        # We access relation data safely via the loaded comic object
        series_name = comic.volume.series.name
        vol_num = comic.volume.volume_number
        context_label = f"{series_name} (vol {vol_num})"

        is_reverse = series_name.lower() in REVERSE_NUMBERING_SERIES

        # Query only what we need for the Python sort
        # Fetch Tuples: (id, number, format, year, month, day)
        query = db.query(
            Comic.id, Comic.number, Comic.format,
            Comic.year, Comic.month, Comic.day
        ).filter(
            Comic.volume_id == comic.volume_id
        )

        if banned_filter is not None:
            query = query.filter(~banned_filter)

        siblings = query.all()

        # Robust Python Sort Logic
        # Priority: Format -> Date -> Number
        def smart_sort_key(x):
            # x[3]=year, x[4]=month, x[5]=day
            y = x[3] if x[3] is not None and x[3] != -1 else 9999
            m = x[4] if x[4] is not None and x[4] != -1 else 99
            d = x[5] if x[5] is not None and x[5] != -1 else 99

            # Number: Natural Sort
            # If reverse, we rely on the caller to flip OR we handle it here.
            # Since standard tuple sort is always ASC, handling reverse number
            # within a multi-key tuple is tricky.
            # Strategy: We Sort by Date/Format FIRST.
            # If dates are identical (common in reverse series), we rely on number.

            # To handle reverse number sort in a tuple, we negate the float value if possible,
            # but natural_sort_key returns a list of strings/ints.
            # Simplification: We will just sort standardly, then post-process?
            # No, post-process is inefficient.

            # Better: We trust that Gimmick Series usually have correct Dates (Countdown does).
            # If Dates are present, Date Sort handles the ordering correctly (May comes before Dec).
            # If Dates are missing, we fall back to Number.

            return (get_format_weight(x[2]), y, m, d, natural_sort_key(x[1]))

        # Apply Sort
        siblings.sort(key=smart_sort_key)

        # Gimmick Fallback:
        # If dates were missing/identical, we might have 1, 2, 3...
        # If it's a reverse series, we want 3, 2, 1.
        # Since we can't easily inject DESC into the tuple sort above for just one field,
        # we check if we should reverse the whole list?
        # NO. We only want to reverse if the series is reverse AND dates didn't do the job.
        # Actually, for Countdown, Dates DO the job.
        # For a series WITHOUT dates that is reverse?
        if is_reverse:
            # Check if dates were effectively useless (all same or missing)
            # If so, reverse the list to get 50, 49, 48...
            # This is a heuristic, but covers the edge case.
            unique_dates = {(x[3], x[4], x[5]) for x in siblings}
            if len(unique_dates) <= 1:
                siblings.reverse()

        ids = [x[0] for x in siblings]

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

    # Page Count Strategy
    # Try DB first (Fast)
    if comic.page_count and comic.page_count > 0:
        page_count = comic.page_count
    else:
        # Fallback to Physical (Slow but accurate)
        # This handles legacy scans or edge cases
        image_service = ImageService()
        page_count = image_service.get_page_count(str(comic.file_path))

        # No Self-heal the DB record here
        # GET requests shouldn't write to DB to avoid locks.
        # The Scanner update will fix this eventually.

    return {
        "comic_id": comic.id,
        "title": comic.title,
        "series_name": comic.volume.series.name,
        "volume_number": comic.volume.volume_number,
        "number": comic.number,
        "page_count": page_count,
        "next_comic_id": next_id,
        "prev_comic_id": prev_id,

        # Context Stats
        # We perform safe math in case the list is empty (edge case)
        "context_position": current_idx + 1 if ids else 0,
        "context_total": len(ids) if ids else 0,
        "context_type": context_type,
        "context_label": context_label
    }


@router.get("/{comic_id}/page/{page_index}", name="comic_page")
def get_comic_page(
        comic_id: int,
        page_index: int,
        db: SessionDep,
        sharpen: Annotated[bool, Query()] = False,
        grayscale: Annotated[bool, Query()] = False,
        webp: Annotated[bool, Query()] = False
):
    """
    Get a specific page image.
    OPTIMIZED: Fetches only the file_path string, not the full Comic object.
    """
    # 1. Fetch Path Only (Scalar Query = <1ms)
    file_path = db.query(Comic.file_path).filter(Comic.id == comic_id).scalar()

    if not file_path:
        raise HTTPException(status_code=404, detail="Comic not found")

    image_service = ImageService()
    image_bytes, is_correct_format, mime_type = image_service.get_page_image(
        str(file_path),
        page_index,
        sharpen=sharpen,
        grayscale=grayscale,
        transcode_webp=webp
    )

    if not image_bytes:
        raise HTTPException(status_code=404, detail="Page not found")

    # 3. Construct Headers
    # We use the returned mime_type to determine the correct extension for the browser
    extension = "webp" if mime_type == "image/webp" else "jpg"

    # Check if the original detected type was PNG/GIF for the filename if we didn't transcode
    if not webp and mime_type == "image/png": extension = "png"
    if not webp and mime_type == "image/gif": extension = "gif"

    # CACHE LOGIC
    headers = {
        "Content-Disposition": f'inline; filename="page_{page_index}.{extension}"'
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
        media_type=mime_type,
        headers=headers
    )
