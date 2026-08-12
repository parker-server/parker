from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import Float, and_, or_
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.sql.expression import func, desc, cast
from typing import Any, List
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from app.core.settings_loader import get_cached_setting
from app.api.deps import SessionDep, CurrentUser
from app.core.comic_helpers import get_format_filters, get_smart_cover, get_series_age_restriction, get_thumbnail_url
from app.models.comic import Comic, Volume
from app.models.interactions import UserComicRating
from app.models.interactions import UserLibraryPin
from app.models.interactions import UserVolumeFollow
from app.models.library import Library
from app.models.series import Series
from app.models.user import User
from app.models.reading_progress import ReadingProgress
from app.schemas.search import ComicSearchItem
from app.core.comic_helpers import REVERSE_NUMBERING_SERIES, NON_PLAIN_FORMATS

router = APIRouter()

HOME_RAIL_LAYOUT_VERSION = 1
HOME_RAIL_DEFINITIONS = (
    {"key": "resume", "title": "Jump Back In", "hint": "Continue books already in progress."},
    {"key": "following_arrivals", "title": "New from Following", "hint": "New issues from followed volumes."},
    {"key": "up_next", "title": "Up Next", "hint": "Next unread issues from recent reads."},
    {"key": "pinned_libraries", "title": "Pinned Libraries", "hint": "Managed by pinned libraries."},
    {"key": "smart_lists", "title": "Smart Lists", "hint": "Managed by smart-list dashboard settings."},
    {"key": "want_to_read", "title": "Want to Read", "hint": "Series you have starred."},
    {"key": "top_rated", "title": "Critically Acclaimed", "hint": "High source ratings."},
    {"key": "top_parker_rated", "title": "Top Rated on Parker", "hint": "Community Parker ratings."},
    {"key": "trending", "title": "Trending", "hint": "Recent anonymous reading activity."},
    {"key": "popular", "title": "Popular with Others", "hint": "Anonymous aggregate reader activity."},
    {"key": "random_gems", "title": "Random Gems", "hint": "A rotating discovery rail."},
    {"key": "recently_added_series", "title": "Recently Added Series", "hint": "Series with newly imported comics."},
    {"key": "recently_updated_series", "title": "Recently Updated Series", "hint": "Series with recently updated comics."},
)
HOME_RAIL_KEYS = tuple(definition["key"] for definition in HOME_RAIL_DEFINITIONS)
HOME_RAIL_DEFINITION_BY_KEY = {definition["key"]: definition for definition in HOME_RAIL_DEFINITIONS}
HOME_RAIL_DEFAULT_INDEX = {key: index for index, key in enumerate(HOME_RAIL_KEYS)}


class HomeRailLayoutEntry(BaseModel):
    key: str
    visible: bool = True


class HomeRailLayoutUpdate(BaseModel):
    rails: list[HomeRailLayoutEntry]


class HomeRailLayoutRail(HomeRailLayoutEntry):
    title: str
    hint: str | None = None
    customized: bool = False


class HomeRailLayoutResponse(BaseModel):
    rails: list[HomeRailLayoutRail]


class HomeRailLayoutError(ValueError):
    pass


def _normalize_saved_home_rail_entries(saved_layout: Any) -> list[dict]:
    if not isinstance(saved_layout, dict):
        return []

    saved_rails = saved_layout.get("rails")
    if not isinstance(saved_rails, list):
        return []

    entries = []
    seen_keys = set()
    for raw_entry in saved_rails:
        if not isinstance(raw_entry, dict):
            continue

        key = raw_entry.get("key")
        if key not in HOME_RAIL_DEFINITION_BY_KEY or key in seen_keys:
            continue

        entries.append({"key": key, "visible": bool(raw_entry.get("visible", True))})
        seen_keys.add(key)

    return entries


def _normalize_submitted_home_rail_entries(rails: list[HomeRailLayoutEntry]) -> list[dict]:
    entries = []
    seen_keys = set()

    for rail in rails:
        key = rail.key
        if key not in HOME_RAIL_DEFINITION_BY_KEY:
            raise HomeRailLayoutError(f"Unknown home rail key: {key}")
        if key in seen_keys:
            raise HomeRailLayoutError(f"Duplicate home rail key: {key}")

        entries.append({"key": key, "visible": bool(rail.visible)})
        seen_keys.add(key)

    return entries


def _insert_missing_home_rail_entries(entries: list[dict]) -> list[dict]:
    resolved = [entry.copy() for entry in entries]
    present_keys = {entry["key"] for entry in resolved}

    for definition in HOME_RAIL_DEFINITIONS:
        key = definition["key"]
        if key in present_keys:
            continue

        entry = {"key": key, "visible": True}
        default_index = HOME_RAIL_DEFAULT_INDEX[key]
        insert_at = len(resolved)

        for candidate_index in range(default_index - 1, -1, -1):
            candidate_key = HOME_RAIL_KEYS[candidate_index]
            existing_index = next(
                (index for index, existing in enumerate(resolved) if existing["key"] == candidate_key),
                None,
            )
            if existing_index is not None:
                insert_at = existing_index + 1
                break
        else:
            for candidate_index in range(default_index + 1, len(HOME_RAIL_KEYS)):
                candidate_key = HOME_RAIL_KEYS[candidate_index]
                existing_index = next(
                    (index for index, existing in enumerate(resolved) if existing["key"] == candidate_key),
                    None,
                )
                if existing_index is not None:
                    insert_at = existing_index
                    break

        resolved.insert(insert_at, entry)
        present_keys.add(key)

    return resolved


def resolve_home_rail_layout(saved_layout: Any) -> dict:
    saved_entries = _normalize_saved_home_rail_entries(saved_layout)
    saved_positions = {entry["key"]: index for index, entry in enumerate(saved_entries)}
    saved_visibility = {entry["key"]: entry["visible"] for entry in saved_entries}

    resolved_entries = _insert_missing_home_rail_entries(saved_entries)

    return {
        "rails": [
            {
                "key": entry["key"],
                "title": HOME_RAIL_DEFINITION_BY_KEY[entry["key"]]["title"],
                "hint": HOME_RAIL_DEFINITION_BY_KEY[entry["key"]]["hint"],
                "visible": entry["visible"],
                "customized": (
                    entry["key"] in saved_positions
                    and (
                        saved_positions[entry["key"]] != HOME_RAIL_DEFAULT_INDEX[entry["key"]]
                        or saved_visibility[entry["key"]] is False
                    )
                ),
            }
            for entry in resolved_entries
        ],
    }


def _persistable_home_rail_layout(entries: list[dict]) -> dict:
    resolved_entries = _insert_missing_home_rail_entries(entries)
    return {
        "version": HOME_RAIL_LAYOUT_VERSION,
        "rails": [
            {"key": entry["key"], "visible": entry["visible"]}
            for entry in resolved_entries
        ],
    }


def _get_layout_user(db: Session, current_user: User) -> User:
    user = db.get(User, current_user.id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/layout", response_model=HomeRailLayoutResponse, name="layout")
def get_home_layout(current_user: CurrentUser):
    return resolve_home_rail_layout(current_user.home_rail_layout)


@router.put("/layout", response_model=HomeRailLayoutResponse, name="update_layout")
def update_home_layout(
        data: HomeRailLayoutUpdate,
        db: SessionDep,
        current_user: CurrentUser,
):
    try:
        entries = _normalize_submitted_home_rail_entries(data.rails)
    except HomeRailLayoutError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    user = _get_layout_user(db, current_user)
    user.home_rail_layout = _persistable_home_rail_layout(entries)
    db.add(user)
    db.commit()
    db.refresh(user)

    return resolve_home_rail_layout(user.home_rail_layout)


@router.post("/layout/reset", response_model=HomeRailLayoutResponse, name="reset_layout")
def reset_home_layout(
        db: SessionDep,
        current_user: CurrentUser,
):
    user = _get_layout_user(db, current_user)
    user.home_rail_layout = None
    db.add(user)
    db.commit()

    return resolve_home_rail_layout(None)

# (Or define a simple one here if ComicSearchItem is too heavy,
# but it should be fine as it matches what comic_card expects)

def format_home_item(
        comic: Comic,
        progress: ReadingProgress = None,
        *,
        rating_mode: str = "none",
        rating_value: float | None = None,
        rating_label: str | None = None,
        parker_rating_average: float | None = None,
        parker_rating_count: int = 0,
) -> dict:
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
        "thumbnail_path": get_thumbnail_url(comic.id, comic.updated_at),
        "community_rating": comic.community_rating,
        "source_rating": comic.community_rating,
        "rating_mode": rating_mode,
        "rating_value": rating_value,
        "rating_label": rating_label,
        "parker_rating_average": parker_rating_average,
        "parker_rating_count": parker_rating_count,
        "progress_percentage": None
    }

    # Calculate percentage if progress is passed
    if progress and comic.page_count and comic.page_count > 0:
        pct = (progress.current_page / comic.page_count) * 100
        item["progress_percentage"] = min(100.0, max(0.0, pct))

    return item

# --- HELPER FOR COVER LOGIC ---
def _pick_best_cover(series_obj, comics_list):
    """
    Selects the best cover from a list of lightweight comic tuples/objects.
    Adapts logic from libraries.py to handle Reverse Numbering.
    """
    if not comics_list:
        return None

    # Helper: Check if format is "Standard"
    def is_standard_format(fmt: str) -> bool:
        if not fmt: return True
        return fmt.lower() not in NON_PLAIN_FORMATS

    # Helper: Safe Sort Key
    def issue_sort_key(c):
        try:
            return float(c.number)
        except:
            return 999999

    # 1. Gimmick Detection
    is_reverse = series_obj.name.lower() in REVERSE_NUMBERING_SERIES

    # 2. Filter for standards
    standards = [c for c in comics_list if is_standard_format(c.format)]
    pool = standards if standards else comics_list

    # 3. Try finding Issue #1 (Only if NOT reverse)
    if not is_reverse:
        issue_ones = [c for c in pool if c.number == '1']
        if issue_ones:
            # If multiple #1s (e.g. multiple volumes), picking the first found or sorting is fine
            return issue_ones[0]

    # 4. Fallback: Sort by number
    pool.sort(key=issue_sort_key)

    # 5. Pick First or Last based on Gimmick
    if is_reverse:
        return pool[-1]  # Highest Number (e.g. Countdown #52)
    else:
        return pool[0]   # Lowest Number (e.g. Spider-Man #10)


def _latest_event_volume_by_series_id(db: Session, series_ids: list[int], timestamp_column):
    if not series_ids:
        return {}

    rows = (
        db.query(
            Volume.series_id,
            Comic.volume_id,
            timestamp_column.label("event_at"),
        )
        .join(Volume)
        .filter(Volume.series_id.in_(series_ids))
        .order_by(
            desc(timestamp_column),
            desc(Volume.volume_number),
            desc(Comic.id),
        )
        .all()
    )

    latest_volume_by_series_id = {}
    for row in rows:
        latest_volume_by_series_id.setdefault(row.series_id, row.volume_id)

    return latest_volume_by_series_id


def _serialize_series_rail_items(db: Session, series_list, cover_volume_by_series_id: dict[int, int] | None = None):
    if not series_list:
        return []

    series_ids = [s.id for s in series_list]

    raw_comics = (
        db.query(
            Comic.id,
            Comic.number,
            Comic.year,
            Comic.format,
            Comic.publisher,
            Comic.updated_at,
            Comic.volume_id,
            Volume.series_id
        )
        .join(Volume)
        .filter(Volume.series_id.in_(series_ids))
        .all()
    )

    series_map = defaultdict(list)
    for rc in raw_comics:
        series_map[rc.series_id].append(rc)

    # Batch-count volumes per series instead of lazy-loading s.volumes per iteration (avoids N+1)
    volume_counts = dict(
        db.query(Volume.series_id, func.count(Volume.id))
        .filter(Volume.series_id.in_(series_ids))
        .group_by(Volume.series_id)
        .all()
    )

    results = []
    for s in series_list:
        s_comics = series_map.get(s.id, [])
        cover_volume_id = cover_volume_by_series_id.get(s.id) if cover_volume_by_series_id else None
        cover_candidates = [c for c in s_comics if c.volume_id == cover_volume_id] if cover_volume_id else s_comics
        first_issue = _pick_best_cover(s, cover_candidates) or _pick_best_cover(s, s_comics)

        if not first_issue:
            continue

        results.append({
            "id": s.id,
            "name": s.name,
            "start_year": first_issue.year,
            "thumbnail_path": get_thumbnail_url(first_issue.id, first_issue.updated_at),
            "publisher": first_issue.publisher,
            "volume_count": volume_counts.get(s.id, 0),
            "starred": False
        })

    return results


def _get_recent_series_by_comic_timestamp(
        db: Session,
        current_user: User,
        *,
        timestamp_column,
        limit: int,
):
    limit = max(1, min(limit, 50))

    latest_event = (
        db.query(
            Volume.series_id.label("series_id"),
            func.max(timestamp_column).label("event_at"),
        )
        .select_from(Comic)
        .join(Volume)
        .group_by(Volume.series_id)
        .subquery()
    )

    query = (
        db.query(Series)
        .join(latest_event, latest_event.c.series_id == Series.id)
    )

    if not current_user.is_superuser:
        allowed_ids = [lib.id for lib in current_user.accessible_libraries]
        if not allowed_ids:
            return []
        query = query.filter(Series.library_id.in_(allowed_ids))

    age_filter = get_series_age_restriction(current_user)
    if age_filter is not None:
        query = query.filter(age_filter)

    recent_series = (
        query
        .order_by(desc(latest_event.c.event_at), Series.name.asc())
        .limit(limit)
        .all()
    )

    cover_volumes = _latest_event_volume_by_series_id(
        db,
        [series.id for series in recent_series],
        timestamp_column,
    )

    return _serialize_series_rail_items(
        db,
        recent_series,
        cover_volume_by_series_id=cover_volumes,
    )


@router.get("/recently-added-series", response_model=List[dict], name="recently_added_series")
def get_recently_added_series(
        db: SessionDep,
        current_user: CurrentUser,
        limit: int = 10,
):
    """
    Get series with recently imported comics, using the latest added volume as
    the representative cover.
    """
    return _get_recent_series_by_comic_timestamp(
        db,
        current_user,
        timestamp_column=Comic.created_at,
        limit=limit,
    )


@router.get("/recently-updated-series", response_model=List[dict], name="recently_updated_series")
def get_recently_updated_series(
        db: SessionDep,
        current_user: CurrentUser,
        limit: int = 10,
):
    """
    Get series with recently updated comics, using the latest updated volume as
    the representative cover.
    """
    return _get_recent_series_by_comic_timestamp(
        db,
        current_user,
        timestamp_column=Comic.updated_at,
        limit=limit,
    )



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

    return _serialize_series_rail_items(db, random_series)


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
        .join(Volume).join(Series) \
        .options(joinedload(Comic.volume).joinedload(Volume.series)) \
        .filter(Comic.community_rating >= 4.0)

    # --- AGE RESTRICTION (Series level) ---
    # Hide "Safe" comics if they belong to a "Banned" series
    age_filter = get_series_age_restriction(current_user)
    if age_filter is not None:
        query = query.filter(age_filter)
    # -----------------------

    gems = query.order_by(desc(Comic.community_rating)).limit(limit).all()

    return [
        format_home_item(
            c,
            rating_mode="source",
            rating_value=c.community_rating,
            rating_label="Source Rating",
        )
        for c in gems
    ]


@router.get("/parker-rated", response_model=List[dict], name="top_parker_rated")
def get_top_parker_rated(
        db: SessionDep,
        current_user: CurrentUser,
        limit: int = 10
):
    """
    Get issues with the highest Parker rating averages.
    Live aggregate for now; this is the main surface likely to benefit from caching later.
    """
    rating_stats = (
        db.query(
            UserComicRating.comic_id.label("comic_id"),
            func.avg(UserComicRating.rating).label("parker_rating_average"),
            func.count(UserComicRating.user_id).label("parker_rating_count"),
        )
        .having(func.count(UserComicRating.user_id) >= 2)
        .group_by(UserComicRating.comic_id)
        .subquery()
    )

    query = (
        db.query(
            Comic,
            rating_stats.c.parker_rating_average,
            rating_stats.c.parker_rating_count,
        )
        .join(rating_stats, rating_stats.c.comic_id == Comic.id)
        .join(Volume).join(Series)
        .options(joinedload(Comic.volume).joinedload(Volume.series))
    )

    age_filter = get_series_age_restriction(current_user)
    if age_filter is not None:
        query = query.filter(age_filter)

    results = (
        query.order_by(
            desc(rating_stats.c.parker_rating_average),
            desc(rating_stats.c.parker_rating_count),
            desc(Comic.updated_at),
        )
        .limit(limit)
        .all()
    )

    if len(results) < 4:
        return []

    return [
        format_home_item(
            comic,
            rating_mode="parker",
            rating_value=float(parker_average) if parker_average is not None else None,
            rating_label="Parker Rating",
            parker_rating_average=float(parker_average) if parker_average is not None else None,
            parker_rating_count=int(parker_count or 0),
        )
        for comic, parker_average, parker_count in results
    ]


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
        .join(Volume).join(Series) \
        .options(joinedload(Comic.volume).joinedload(Volume.series)) \
        .filter(
        ReadingProgress.user_id == current_user.id,
        ReadingProgress.completed == False,
        ReadingProgress.current_page > 0
    )

    # --- AGE RESTRICTION (Series level) ---
    age_filter = get_series_age_restriction(current_user)
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


@router.get("/following-arrivals", response_model=List[ComicSearchItem], name="following_arrivals")
def get_following_arrivals(
        db: SessionDep,
        current_user: CurrentUser,
        limit: int = 10,
):
    """
    Get newly arrived plain issues from followed volumes.
    Uses the follow timestamp as the baseline and excludes comics the user has
    already started or completed.
    """

    is_plain, _, _ = get_format_filters()

    not_started_or_read = or_(
        ReadingProgress.id == None,
        and_(
            ReadingProgress.completed == False,
            or_(ReadingProgress.current_page == None, ReadingProgress.current_page <= 0),
        ),
    )

    query = (
        db.query(Comic)
        .join(UserVolumeFollow, UserVolumeFollow.volume_id == Comic.volume_id)
        .join(Volume).join(Series)
        .outerjoin(
            ReadingProgress,
            and_(
                ReadingProgress.comic_id == Comic.id,
                ReadingProgress.user_id == current_user.id,
            ),
        )
        .options(joinedload(Comic.volume).joinedload(Volume.series))
        .filter(UserVolumeFollow.user_id == current_user.id)
        .filter(Comic.created_at > UserVolumeFollow.followed_at)
        .filter(is_plain)
        .filter(not_started_or_read)
    )

    if not current_user.is_superuser:
        allowed_ids = [lib.id for lib in current_user.accessible_libraries]
        query = query.filter(Series.library_id.in_(allowed_ids))

    age_filter = get_series_age_restriction(current_user)
    if age_filter is not None:
        query = query.filter(age_filter)

    results = (
        query.order_by(
            desc(Comic.created_at),
            desc(cast(Comic.number, Float)),
            desc(Comic.number),
        )
        .limit(limit)
        .all()
    )

    return [format_home_item(comic) for comic in results]


@router.get("/pinned-libraries", response_model=List[dict], name="pinned_libraries")
def get_pinned_libraries(
        db: SessionDep,
        current_user: CurrentUser,
        limit: int = 15,
):
    """
    Get home-page rails for libraries the current user pinned.
    Each pinned library rail contains recently updated series for that library.
    """
    limit = max(1, min(limit, 15))

    pins_query = (
        db.query(UserLibraryPin, Library)
        .join(Library, UserLibraryPin.library_id == Library.id)
        .filter(UserLibraryPin.user_id == current_user.id)
        .order_by(UserLibraryPin.pinned_at.asc(), Library.name.asc())
    )

    if not current_user.is_superuser:
        allowed_ids = [lib.id for lib in current_user.accessible_libraries]
        if not allowed_ids:
            return []

        pins_query = pins_query.filter(UserLibraryPin.library_id.in_(allowed_ids))

    pinned_libraries = pins_query.all()
    if not pinned_libraries:
        return []

    age_filter = get_series_age_restriction(current_user)
    rails = []

    for _pin, library in pinned_libraries:
        recent_series_query = (
            db.query(Series)
            .join(Volume)
            .join(Comic)
            .filter(Series.library_id == library.id)
        )

        if age_filter is not None:
            recent_series_query = recent_series_query.filter(age_filter)

        recent_series = (
            recent_series_query
            .group_by(Series.id)
            .order_by(desc(func.max(Comic.updated_at)), Series.name.asc())
            .limit(limit)
            .all()
        )

        cover_volumes = _latest_event_volume_by_series_id(
            db,
            [series.id for series in recent_series],
            Comic.updated_at,
        )
        items = _serialize_series_rail_items(
            db,
            recent_series,
            cover_volume_by_series_id=cover_volumes,
        )
        if not items:
            continue

        rails.append({
            "library_id": library.id,
            "name": library.name,
            "items": items,
        })

    return rails


@router.get("/trending", response_model=List[dict], name="trending")
def get_trending(
        db: SessionDep,
        current_user: CurrentUser,
        limit: int = 10
):
    """
    Get time-sensitive trending series based on recent opted-in reading activity.
    Ranked by recent activity volume, then distinct readers, then freshest activity.
    """
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=30)

    trending_series_query = (
        db.query(Series)
        .join(Volume).join(Comic).join(ReadingProgress)
        .join(User)
        .filter(User.social_insights_enabled == True)
        .filter(ReadingProgress.user_id != current_user.id)
        .filter(ReadingProgress.last_read_at >= cutoff_date)
    )

    age_filter = get_series_age_restriction(current_user)
    if age_filter is not None:
        trending_series_query = trending_series_query.filter(age_filter)

    trending_series = (
        trending_series_query.group_by(Series.id)
        .order_by(
            desc(func.count(ReadingProgress.id)),
            desc(func.count(func.distinct(ReadingProgress.user_id))),
            desc(func.max(ReadingProgress.last_read_at)),
        )
        .limit(limit)
        .all()
    )

    if len(trending_series) < 4:
        return []

    results = _serialize_series_rail_items(db, trending_series)

    if len(results) < 4:
        return []

    return results


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
    age_filter = get_series_age_restriction(current_user)

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

        # Base Next Query
        # Explicit Join Volume/Series for Filter
        next_query = db.query(Comic) \
            .join(Volume).join(Series) \
            .options(joinedload(Comic.volume).joinedload(Volume.series)) \
            .filter(Comic.volume_id == progress.comic.volume_id)

        if is_reverse:
            # Find next issue (LOWER number)
            # We want the largest number that is SMALLER than current
            # e.g. Current=51, we want 50.
            next_query = next_query.filter(cast(Comic.number, Float) < current_number)

        else:
            # Standard Logic (Higher number)
            # Find the next comic
            # We're still going to run 1 query here per series, but it's very fast on SQLite
            # because it's a simple indexed lookup on (volume_id, number).
            next_query = next_query.filter(cast(Comic.number, Float) > current_number)

        # --- APPLY AGE FILTER TO SUGGESTION (Series level) ---
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
    Get "Popular with Others" series based on other users' reading activity.
    Respects the anonymous social insights preference.
    Secured for age rating
    """

    # 1. Aggregation Query
    # Find Series with the most DISTINCT users reading them (excluding me)
    popular_series_query = (
        db.query(Series)
        .join(Volume).join(Comic).join(ReadingProgress)
        .join(User)
        .filter(User.social_insights_enabled == True)
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

    results = _serialize_series_rail_items(db, popular_series)

    # Secondary Guard: Ensure we still have enough items after cover validation
    if len(results) < 4:
        return []

    return results
