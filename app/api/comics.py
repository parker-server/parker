from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, FileResponse
from sqlalchemy import Float, func, case, cast, or_
from sqlalchemy.orm import joinedload, selectinload
from typing import List, Annotated, Literal
from pathlib import Path
import re
import random

from app.core.comic_helpers import (get_reading_time, get_format_sort_index, REVERSE_NUMBERING_SERIES,
                                    get_age_rating_config, get_series_age_restriction)
from app.api.deps import SessionDep, CurrentUser, ComicDep

from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.library import Library
from app.models.credits import Person, ComicCredit
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.collection import Collection, CollectionItem
from app.models.pull_list import PullList, PullListItem
from app.models.reading_progress import ReadingProgress
from app.models.tags import Character, Team, Location, Genre

from app.schemas.search import SearchRequest, SearchResponse
from app.services.search import SearchService
from app.services.images import ImageService


router = APIRouter()

# --- SECURITY HELPER ---
def filter_by_user_access(query, user: CurrentUser):
    """
    Applies Row Level Security (RLS) to a query involving Comics/Series.
    Assumes the query can resolve Series.library_id (requires joins if not already present).
    """
    if user.is_superuser:
        return query

    allowed_ids = [lib.id for lib in user.accessible_libraries]
    return query.filter(Series.library_id.in_(allowed_ids))

def natural_sort_key(s):
    """Sorts 'Issue 1' before 'Issue 10' and handles '10a'"""
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split('([0-9]+)', str(s))]

@router.post("/search", response_model=SearchResponse, name="search")
async def search_comics(request: SearchRequest, db: SessionDep, current_user: CurrentUser):
    """
    Search comics with complex filters

    Example request:
```json
    {
      "match": "all",
      "filters": [
        {"field": "character", "operator": "contains", "value": ["Batman", "Superman"]},
        {"field": "year", "operator": "equal", "value": 1985},
        {"field": "publisher", "operator": "equal", "value": "DC Comics"}
      ],
      "sort_by": "year",
      "sort_order": "desc",
      "limit": 50
    }
```
    """
    search_service = SearchService(db, current_user)
    results = search_service.search(request)
    return results

@router.get("/{comic_id}", name="detail")
async def get_comic(comic_id: int, db: SessionDep, current_user: CurrentUser):
    """
    Get a specific comic with all metadata.
    OPTIMIZED: Uses 'selectinload' for lists to prevent Cartesian Product explosion.
    """

    # 1. Fetch Comic with optimized loading strategy
    # - joinedload: Good for "One-to-One" or "Many-to-One" (Parents) -> JOINs in SQL
    # - selectinload: Good for "One-to-Many" (Lists) -> Separate fast queries
    comic = db.query(Comic).options(
        # Parents: Join them (1 row)
        joinedload(Comic.volume).joinedload(Volume.series).joinedload(Series.library),

        # Children/Lists: Select them separately to avoid 10x10x10 row explosion
        selectinload(Comic.credits).joinedload(ComicCredit.person),  # Keep person joined to credit
        selectinload(Comic.characters),
        selectinload(Comic.teams),
        selectinload(Comic.locations),
        selectinload(Comic.genres)
    ).filter(Comic.id == comic_id).first()

    if not comic:
        raise HTTPException(status_code=404, detail="Comic not found")

    # 2. Security Check (Manual RLS since we aren't using the dependency)
    if not current_user.is_superuser:
        allowed_libs = {l.id for l in current_user.accessible_libraries}
        if comic.volume.series.library_id not in allowed_libs:
            raise HTTPException(status_code=404, detail="Comic not found")

    # --- AGE RATING CHECK ---
    # We have the comic loaded. We can check python side to save a query.
    if not current_user.is_superuser and current_user.max_age_rating:

        allowed, banned = get_age_rating_config(current_user)

        is_banned = False

        # Check explicit ban
        if comic.age_rating in banned:
            is_banned = True

        # Check Unknowns
        if not current_user.allow_unknown_age_ratings:
            if not comic.age_rating or comic.age_rating == "" or comic.age_rating.lower() == "unknown":
                is_banned = True

        if is_banned:
            raise HTTPException(status_code=403, detail="Content restricted by age rating")
    # ------------------------



    # Calculate Reading Time
    total_pages = comic.page_count or 0
    read_time = get_reading_time(total_pages)

    # Build credits dictionary by role
    # This loop is now safe (in-memory) because we eager loaded credits+persons
    credits = {}
    for credit in comic.credits:
        if credit.role not in credits:
            credits[credit.role] = []
        credits[credit.role].append(credit.person.name)

    # Check Progress
    read_status = "new"
    progress = db.query(ReadingProgress).filter(
        ReadingProgress.comic_id == comic.id,
        ReadingProgress.user_id == current_user.id
    ).first()

    # If started but not finished, show "Continue"
    if progress and not progress.completed and progress.current_page > 0:
        read_status = "in_progress"

    return {
        "id": comic.id,
        "filename": comic.filename,
        "file_path": comic.file_path,
        "file_size": comic.file_size,

        # Library info
        "library_id": comic.volume.series.library_id,
        "library_name": comic.volume.series.library.name,

        # Series info
        "series_id": comic.volume.series.id,
        "series": comic.volume.series.name,
        "volume": comic.volume.volume_number,
        "number": comic.number,
        "title": comic.title,
        "summary": comic.summary,
        "web": comic.web,
        "notes": comic.notes,

        # Date
        "year": comic.year,
        "month": comic.month,
        "day": comic.day,

        # Credits (grouped by role)
        "credits": credits,

        # Publishing
        "publisher": comic.publisher,
        "imprint": comic.imprint,
        "format": comic.format,
        "series_group": comic.series_group,

        # Technical
        "page_count": comic.page_count,
        "read_time": read_time,
        "scan_information": comic.scan_information,

        # Misc
        "age_rating": comic.age_rating,
        "language_iso": comic.language_iso,
        "community_rating": comic.community_rating,

        # Tags
        "characters": [c.name for c in comic.characters],
        "teams": [t.name for t in comic.teams],
        "locations": [l.name for l in comic.locations],
        "genres": [g.name for g in comic.genres],

        # Reading lists
        "alternate_series": comic.alternate_series,
        "alternate_number": comic.alternate_number,
        "story_arc": comic.story_arc,

        # Timestamps
        "created_at": comic.created_at,
        "updated_at": comic.updated_at,

        # Read status
        "read_status": read_status,

        # ColorScape data
        "color_palette": comic.color_palette,
    }


@router.get("/{comic_id}/thumbnail", name="thumbnail")
async def get_comic_thumbnail(
        comic_id: int,
        db: SessionDep
):
    """
        Get the thumbnail for a comic (public)
    Serves from storage/cover. Regenerates if missing.
        Self-healing: Generates file if missing, but DOES NOT write to DB
        to avoid locking issues during parallel loading.
    """
    # 1. Base Query
    comic = db.query(Comic).filter(Comic.id == comic_id).first()

    if not comic:
        # We return 404 here to prevent leaking existence of the comic
        raise HTTPException(status_code=404, detail="Comic not found")


    # 2. Layer 1: Check the path stored in the Database
    if comic.thumbnail_path:
        db_path = Path(comic.thumbnail_path)
        if db_path.exists():
            return FileResponse(db_path, media_type="image/webp")

    # 3. Layer 2: Check the "Standard" path (Self-Healing fallback)
    # This handles cases where the DB is NULL or points to a file that was deleted.
    standard_path = Path(f"./storage/cover/comic_{comic.id}.webp")

    if standard_path.exists():
        return FileResponse(standard_path, media_type="image/webp")

    # 4. Layer 3: Generate on the fly
    # We use the standard path for the new file.
    image_service = ImageService()
    success = image_service.generate_thumbnail(comic.file_path, standard_path)

    if not success:
        # Return a placeholder or 404
        raise HTTPException(status_code=404, detail="Could not generate thumbnail")

    # NOTE: We serve the file, but we DO NOT write back to the DB here.
    # This avoids the "Database Locked" issues during parallel loading.
    # The next time this runs, it will hit Layer 2 and succeed.
    return FileResponse(standard_path, media_type="image/webp")


@router.get("/random/backgrounds", name="random_backgrounds")
async def get_random_backgrounds(
        db: SessionDep,
        limit: int = 20
):
    """
    Get a list of random comic thumbnail URLs for the login background.
    Optimized for performance: Avoids SQL 'ORDER BY RANDOM()' sorting.
    """
    # 1. Fetch ALL eligible IDs (Linear scan, fast)
    # We only fetch the ID column to minimize memory usage
    all_ids_query = db.query(Comic.id).filter(Comic.thumbnail_path != None).all()

    # SQLAlchemy returns a list of tuples like [(1,), (2,), (5,)]
    # We flatten this to a standard list [1, 2, 5]
    all_ids = [r[0] for r in all_ids_query]

    if not all_ids:
        return []

    # 2. Python Sample (Instant)
    # Safe handling if we have fewer comics than the requested limit
    sample_size = min(len(all_ids), limit)
    selected_ids = random.sample(all_ids, sample_size)

    # 3. Construct URLs (No extra DB query needed)
    return [f"api/comics/{cid}/thumbnail" for cid in selected_ids]


@router.get("/covers/manifest", name="cover_manifest")
async def get_cover_manifest(
        db: SessionDep,
        current_user: CurrentUser,
        context_type: Literal["series", "volume", "reading_list", "collection", "pull_list"],
        context_id: int
):
    """
    Returns a list of Comic IDs and Titles to power the Cover Browser.
    Handles Reverse Numbering (Countdown) and Date Sorting (Zero Hour).
    """

    # 1. Base Query
    # OPTIMIZED: Uses explicit labels to ensure 'Series Name' and 'Comic Title' don't collide.
    query = db.query(
        Comic.id,
        Comic.title,
        Comic.number,
        Volume.volume_number,
        Series.name.label("series_name")
    ) \
        .select_from(Comic) \
        .join(Volume) \
        .join(Series)

    # Apply Security
    query = filter_by_user_access(query, current_user)

    # --- AGE RATING FILTER ---
    # Switch to Series Level check.
    # Prevents leaking covers of "Safe" issues that belong to "Banned" series.
    age_filter = get_series_age_restriction(current_user)
    if age_filter is not None:
        query = query.filter(age_filter)
    # -------------------------


    # --- DEFINE SORT LOGIC (Shared with comic_helpers) ---
    # Push NULL/-1 dates to bottom (9999)
    sort_year = case((or_(Comic.year == None, Comic.year == -1), 9999), else_=Comic.year)
    sort_month = case((or_(Comic.month == None, Comic.month == -1), 99), else_=Comic.month)
    sort_day = case((or_(Comic.day == None, Comic.day == -1), 99), else_=Comic.day)
    sort_number = cast(Comic.number, Float)


    # 3. Context Filtering & Sorting
    if context_type == "volume":

        # Check for Gimmick Series Name via simple scalar query first
        # (Optimization: We could join, but explicit check is safer for logic branching)
        series_name = db.query(Series.name).join(Volume).filter(Volume.id == context_id).scalar()

        number_direction = sort_number.asc()
        if series_name and series_name.lower() in REVERSE_NUMBERING_SERIES:
            number_direction = sort_number.desc()

        query = query.filter(Comic.volume_id == context_id) \
            .order_by(sort_year.asc(), sort_month.asc(), sort_day.asc(),
            number_direction)

    elif context_type == "series":

        # Check Name
        series_name = db.query(Series.name).filter(Series.id == context_id).scalar()

        number_direction = sort_number.asc()
        if series_name and series_name.lower() in REVERSE_NUMBERING_SERIES:
            number_direction = sort_number.desc()

        format_weight = get_format_sort_index()
        query = query.filter(Volume.series_id == context_id) \
            .order_by(Volume.volume_number, format_weight,
                      sort_year.asc(), sort_month.asc(), sort_day.asc(), number_direction)

    elif context_type == "reading_list":
        # Explicit Join: Join ReadingListItem to Comic
        query = query.join(ReadingListItem, ReadingListItem.comic_id == Comic.id) \
            .filter(ReadingListItem.reading_list_id == context_id) \
            .order_by(ReadingListItem.position)

    elif context_type == "pull_list":
        # Explicit Join: Join PullListItem to Comic
        # Also ensure we only show the current user's list (though RLS on library covers the content)
        query = query.join(PullListItem, PullListItem.comic_id == Comic.id) \
            .filter(PullListItem.pull_list_id == context_id) \
            .order_by(PullListItem.sort_order)

    elif context_type == "collection":
        # Explicit Join: Join CollectionItem to Comic
        # Fixes "Can't determine which FROM clause" error
        query = query.join(CollectionItem, CollectionItem.comic_id == Comic.id) \
            .filter(CollectionItem.collection_id == context_id) \
            .order_by(Comic.year.asc(), Series.name.asc(), func.cast(Comic.number, Float))

    items = query.all()

    return {
        "total": len(items),
        "items": [
            {
                "comic_id": r.id,
                # Explicitly use the labeled series name
                "label": f"{r.series_name} #{r.number}",
                "thumbnail_url": f"/api/comics/{r.id}/thumbnail"
            }
            for r in items
        ]
    }