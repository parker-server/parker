from fastapi import APIRouter, Depends
from sqlalchemy import Float
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.sql.expression import func, desc, cast
from typing import List
from datetime import datetime, timezone, timedelta

from app.core.settings_loader import get_cached_setting
from app.api.deps import SessionDep, CurrentUser
from app.core.comic_helpers import get_smart_cover, get_comic_age_restriction, get_series_age_restriction
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.user import User
from app.models.reading_progress import ReadingProgress
from app.schemas.search import ComicSearchItem
from app.core.comic_helpers import REVERSE_NUMBERING_SERIES

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

@router.get("/random", response_model=List[dict], name="random_gems")
def get_random_gems(
        db: SessionDep,
        current_user: CurrentUser,
        limit: int = 10
):

    """
    Get random Series. Great for 'Spin the Wheel' discovery.
    Secured with Age Restrictions.
    """
    # 1. Query Random Series
    query = db.query(Series)

    # --- AGE RESTRICTION ---
    age_filter = get_series_age_restriction(current_user)
    if age_filter is not None:
        query = query.filter(age_filter)
    # -----------------------

    random_series = query.order_by(func.random()).limit(limit).all()

    if not random_series:
        return []

    # 2. Batch Fetch Covers
    # Instead of looping and querying, we grab the first comic for all these series at once.
    series_ids = [s.id for s in random_series]

    # Subquery: Rank comics by number within each series (Partition by Series, Order by Number)
    # This assigns 'rn=1' to the first issue of every series.
    subquery = (
        db.query(
            Comic.id,
            func.row_number().over(
                partition_by=Volume.series_id,
                order_by=cast(Comic.number, Float).asc()
            ).label("rn")
        )
        .join(Volume)
        .filter(Volume.series_id.in_(series_ids))
        .subquery()
    )

    # Main Query: Join against the subquery and keep only the #1s
    covers = db.query(Comic) \
        .join(subquery, Comic.id == subquery.c.id) \
        .filter(subquery.c.rn == 1) \
        .all()

    # Map series_id -> cover_comic
    # (We need to access volume.series_id, so ensure it's loaded or accessed via cover.volume_id map)
    cover_map = {}
    for c in covers:
        # We know the volume is loaded because we joined it in the subquery logic,
        # but to be safe with ORM objects:
        if c.volume:
            cover_map[c.volume.series_id] = c


    results = []
    for s in random_series:

        first_issue = cover_map.get(s.id)

        # Fallback: If logic missed (e.g. empty series), skip
        if not first_issue:
            continue

        results.append({
            "id": s.id,
            "name": s.name,
            "start_year": first_issue.year,
            "thumbnail_path": f"/api/comics/{first_issue.id}/thumbnail",
            # Add extra metadata for the Series Card if needed
            "publisher": first_issue.publisher,
            "volume_count": len(s.volumes) if s.volumes else 0,
            "starred": False  # You can query UserSeries if you want this accurate
        })

    return results


@router.get("/rated", response_model=List[ComicSearchItem], name="top_rated")
def get_top_rated(
        db: SessionDep,
        current_user: CurrentUser,
        limit: int = 10
):
    """
    Get issues with High Community Rating (4.0+).
    Eager loads relationships to avoid N+1.
    Secured with age restriction
    """
    query = db.query(Comic) \
        .options(joinedload(Comic.volume).joinedload(Volume.series)) \
        .filter(Comic.community_rating >= 4.0)

    # --- AGE RESTRICTION ---
    age_filter = get_comic_age_restriction(current_user)
    if age_filter is not None:
        query = query.filter(age_filter)
    # -----------------------

    gems = query.order_by(desc(Comic.community_rating)).limit(limit).all()

    return [format_home_item(c) for c in gems]


@router.get("/resume", response_model=List[ComicSearchItem], name="resume_reading")
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

    # --- AGE RESTRICTION ---
    age_filter = get_comic_age_restriction(current_user)
    if age_filter is not None:
        query = query.filter(age_filter)
    # -----------------------

    # 3. Apply Staleness Filter
    if cutoff_date:
        query = query.filter(ReadingProgress.last_read_at >= cutoff_date)

    results = query.order_by(desc(ReadingProgress.last_read_at)) \
        .limit(limit) \
        .all()

    return [format_home_item(c, p) for c, p in results]


@router.get("/up-next", response_model=List[ComicSearchItem], name="up_next")
def get_up_next(
        db: SessionDep,
        current_user: CurrentUser,
        limit: int = 10
):
    """
    Get the NEXT issue for series recently read. Handles Reverse Numbering
    Secured for age rating
    """

    # 1. Calculate Cutoff (Reuse the same setting for consistency)
    staleness_weeks = get_cached_setting("ui.on_deck.staleness_weeks", default=4)
    cutoff_date = None
    if staleness_weeks > 0:
        cutoff_date = datetime.now(timezone.utc) - timedelta(weeks=staleness_weeks)

    # 2. Get recently completed comics
    # Eager load Series/Volume here so we don't query them later in the loop
    # Note: We don't filter HISTORY by age, because you already read it.
    # We WILL filter the suggestion (the next book).
    history_query = db.query(ReadingProgress) \
        .join(Comic).join(Volume).join(Series) \
        .options(joinedload(ReadingProgress.comic).joinedload(Comic.volume).joinedload(Volume.series)) \
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

    # Pre-fetch "Read" status
    # Instead of querying the DB 50 times to see if we read the *next* book,
    # we load all completed comic IDs for this user into memory once.
    read_comic_ids = set(
        row[0] for row in db.query(ReadingProgress.comic_id)
        .filter(ReadingProgress.user_id == current_user.id, ReadingProgress.completed == True)
        .all()
    )

    seen_series = set()
    results = []

    # Prepare Age Filter for the loop
    age_filter = get_comic_age_restriction(current_user)

    for progress in recent_history:
        # Access via eager loaded relationship (no query)

        series_id = progress.comic.volume.series.name  # Access name for check
        series_obj = progress.comic.volume.series  # Keep object reference

        if series_obj.id in seen_series:
            continue

        seen_series.add(series_obj.id)

        try:
            current_number = float(progress.comic.number)
        except (ValueError, TypeError):
            continue

        # GIMMICK LOGIC
        # If Reverse (Countdown): Next issue is Current - 1
        # If Standard: Next issue is Current + 1
        is_reverse = series_obj.name.lower() in REVERSE_NUMBERING_SERIES

        if is_reverse:
            # Find next issue (LOWER number)
            # We want the largest number that is SMALLER than current
            # e.g. Current=51, we want 50.
            next_query = db.query(Comic) \
                .options(joinedload(Comic.volume).joinedload(Volume.series)) \
                .filter(
                Comic.volume_id == progress.comic.volume_id,
                cast(Comic.number, Float) < current_number
            )

        else:
            # Standard Logic (Higher number)
            # Find the next comic
            # We're still going to run 1 query here per series, but it's very fast on SQLite
            # because it's a simple indexed lookup on (volume_id, number).
            next_query = db.query(Comic) \
                .options(joinedload(Comic.volume).joinedload(Volume.series)) \
                .filter(
                Comic.volume_id == progress.comic.volume_id,
                cast(Comic.number, Float) > current_number
            )

        # --- APPLY AGE FILTER TO SUGGESTION ---
        if age_filter is not None:
            next_query = next_query.filter(age_filter)
        # --------------------------------------

        if is_reverse:
            next_comic = next_query.order_by(cast(Comic.number, Float).desc()).first()
        else:
            next_comic = next_query.order_by(cast(Comic.number, Float).asc()).first()

        if next_comic:
            # Check memory set instead of DB
            if next_comic.id not in read_comic_ids:
                results.append(format_home_item(next_comic))

        if len(results) >= limit:
            break

    return results

@router.get("/popular", response_model=List[dict], name="popular")
def get_popular(
        db: SessionDep,
        current_user: CurrentUser,
        limit: int = 10
):
    """
    Get 'Trending' Series based on other users' reading activity.
    Respects the 'share_progress_enabled' privacy setting.
    Secured for age rating
    """

    # 1. Aggregation Query
    # Find Series with the most DISTINCT users reading them (excluding me)
    popular_series_query = (
        db.query(Series)
        .join(Volume).join(Comic).join(ReadingProgress)
        .join(User)
        .filter(User.share_progress_enabled == True)
        .filter(ReadingProgress.user_id != current_user.id) # Dont include ourselves
    )

    # --- AGE RESTRICTION ---
    # Apply Series Level Poison Pill
    age_filter = get_series_age_restriction(current_user)
    if age_filter is not None:
        popular_series_query = popular_series_query.filter(age_filter)
    # -----------------------

    popular_series = (
        popular_series_query.group_by(Series.id)
        .order_by(func.count(ReadingProgress.user_id.distinct()).desc())
        .limit(limit)
        .all()
    )

    # Guard: Minimum Threshold (User UX preference)
    if len(popular_series) < 4:
        return []

    # 2. Batch Fetch Covers (SQLite Compatible)
    series_ids = [s.id for s in popular_series]

    # Subquery: Rank comics by number within each series (Partition by Series, Order by Number)
    subquery = (
        db.query(
            Comic.id,
            func.row_number().over(
                partition_by=Volume.series_id,
                order_by=cast(Comic.number, Float).asc()
            ).label("rn")
        )
        .join(Volume)
        .filter(Volume.series_id.in_(series_ids))
        .subquery()
    )

    # Main Query: Join against the subquery and keep only the #1s
    covers = db.query(Comic) \
        .join(subquery, Comic.id == subquery.c.id) \
        .filter(subquery.c.rn == 1) \
        .options(joinedload(Comic.volume)) \
        .all()

    # Map series_id -> cover_comic
    cover_map = {}
    for c in covers:
        if c.volume:
            cover_map[c.volume.series_id] = c

    # 3. Format Results
    results = []
    for s in popular_series:
        first_issue = cover_map.get(s.id)

        if not first_issue:
            continue

        results.append({
            "id": s.id,
            "name": s.name,
            "start_year": first_issue.year,
            "thumbnail_path": f"/api/comics/{first_issue.id}/thumbnail",
            "publisher": first_issue.publisher,
            "volume_count": len(s.volumes) if s.volumes else 0,
            "starred": False
        })

    # Secondary Guard: Ensure we still have enough items after cover validation
    if len(results) < 4:
        return []

    return results