from fastapi import APIRouter, Depends
from sqlalchemy import Float
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.sql.expression import func, desc, cast
from typing import List
from datetime import datetime, timezone, timedelta

from app.core.settings_loader import get_cached_setting
from app.api.deps import SessionDep, CurrentUser
from app.models.comic import Comic, Volume
from app.models.reading_progress import ReadingProgress
from app.schemas.search import ComicSearchItem

router = APIRouter()

# (Or define a simple one here if ComicSearchItem is too heavy,
# but it should be fine as it matches what comic_card expects)

def format_home_item(comic: Comic, progress: ReadingProgress = None) -> dict:
    """Helper to flatten Comic object into ComicSearchItem schema"""
    item = {
        "id": comic.id,
        # Handle potential missing relationships safely
        "series": comic.volume.series.name if comic.volume and comic.volume.series else "Unknown",
        "volume": comic.volume.volume_number if comic.volume else 0,
        "number": comic.number,
        "title": comic.title,
        "year": comic.year,
        "publisher": comic.publisher,
        "format": comic.format,
        "thumbnail_path": f"/api/comics/{comic.id}/thumbnail",
        "community_rating": comic.community_rating,
        "progress_percentage": None
    }

    # Calculate percentage if progress is passed
    if progress and comic.page_count and comic.page_count > 0:
        pct = (progress.current_page / comic.page_count) * 100
        item["progress_percentage"] = min(100.0, max(0.0, pct))

    return item

@router.get("/random", response_model=List[ComicSearchItem])
def get_random_gems(
        db: SessionDep,
        current_user: CurrentUser,
        limit: int = 10
):

    """
    Get random issues. Great for 'Spin the Wheel' discovery.
    """
    # SQLite/Postgres 'ORDER BY RANDOM()'
    # Optimization: Filter out very short things (like art books) if you have page counts?
    # For now, just simple random.
    gems = db.query(Comic) \
        .order_by(func.random()) \
        .limit(limit) \
        .all()

    return [format_home_item(c) for c in gems]


@router.get("/rated", response_model=List[ComicSearchItem])
def get_top_rated(
        db: SessionDep,
        current_user: CurrentUser,
        limit: int = 10
):
    """
    Get issues with High Community Rating (4.0+).
    """
    gems = db.query(Comic) \
        .filter(Comic.community_rating >= 4.0) \
        .order_by(desc(Comic.community_rating)) \
        .limit(limit) \
        .all()

    return [format_home_item(c) for c in gems]


@router.get("/resume", response_model=List[ComicSearchItem])
def get_resume_reading(
        db: SessionDep,
        current_user: CurrentUser,
        limit: int = 10
):
    """Get 'In Progress' issues, respecting staleness settings."""

    # 1. Calculate Cutoff
    staleness_weeks = get_cached_setting("ui.on_deck.staleness_weeks", default=4)
    cutoff_date = None
    if staleness_weeks > 0:
        cutoff_date = datetime.now(timezone.utc) - timedelta(weeks=staleness_weeks)

    # 2. Build Query
    query = db.query(Comic, ReadingProgress) \
        .join(ReadingProgress) \
        .options(joinedload(Comic.volume).joinedload(Volume.series)) \
        .filter(
        ReadingProgress.user_id == current_user.id,
        ReadingProgress.completed == False,
        ReadingProgress.current_page > 0
    )

    # 3. Apply Staleness Filter
    if cutoff_date:
        query = query.filter(ReadingProgress.last_read_at >= cutoff_date)

    results = query.order_by(desc(ReadingProgress.last_read_at)) \
        .limit(limit) \
        .all()

    return [format_home_item(c, p) for c, p in results]


@router.get("/up-next", response_model=List[ComicSearchItem])
def get_up_next(
        db: SessionDep,
        current_user: CurrentUser,
        limit: int = 10
):
    """Get the NEXT issue for series recently read."""

    # 1. Calculate Cutoff (Reuse the same setting for consistency)
    staleness_weeks = get_cached_setting("ui.on_deck.staleness_weeks", default=4)
    cutoff_date = None
    if staleness_weeks > 0:
        cutoff_date = datetime.now(timezone.utc) - timedelta(weeks=staleness_weeks)

    # 2. Get recently completed comics
    history_query = db.query(ReadingProgress) \
        .join(Comic) \
        .options(joinedload(ReadingProgress.comic).joinedload(Comic.volume)) \
        .filter(
        ReadingProgress.user_id == current_user.id,
        ReadingProgress.completed == True
    )

    # 3. Apply Staleness Filter
    # (Don't suggest next issues for series I finished years ago)
    if cutoff_date:
        history_query = history_query.filter(ReadingProgress.last_read_at >= cutoff_date)

    recent_history = history_query.order_by(desc(ReadingProgress.last_read_at)) \
        .limit(50) \
        .all()

    # ... (Keep the rest of the 'Next Issue' logic: seen_series, finding next number, etc.) ...

    seen_series = set()
    results = []

    for progress in recent_history:
        # ... logic to find next comic ...
        # (Same as previous step)
        series_id = progress.comic.volume.series_id
        if series_id in seen_series:
            continue
        seen_series.add(series_id)

        try:
            current_number = float(progress.comic.number)
        except (ValueError, TypeError):
            continue

        next_comic = db.query(Comic) \
            .filter(
            Comic.volume_id == progress.comic.volume_id,
            cast(Comic.number, Float) > current_number
        ) \
            .order_by(cast(Comic.number, Float).asc()) \
            .first()

        if next_comic:
            is_already_read = db.query(ReadingProgress).filter(
                ReadingProgress.user_id == current_user.id,
                ReadingProgress.comic_id == next_comic.id,
                ReadingProgress.completed == True
            ).first()

            if not is_already_read:
                # Manually populate series name to avoid validation error if lazy loading misses it
                if not next_comic.volume.series:
                    # Force load if needed, or rely on Session identity map
                    pass
                results.append(format_home_item(next_comic))

        if len(results) >= limit:
            break

    return results

