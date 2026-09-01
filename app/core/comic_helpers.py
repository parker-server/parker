import logging
from datetime import datetime, timezone
from functools import lru_cache
from sqlalchemy import func, or_, not_, case, cast, Float
from fastapi import HTTPException

from app.api.deps import SessionDep
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.tags import Character, Team, Location, Genre
from app.models.credits import Person, ComicCredit
from app.models.reading_progress import ReadingProgress

logger = logging.getLogger(__name__)


# Titles that number backwards (Countdown) or count down to 0 (Zero Hour)
# where the Highest Number is actually the Debut/Cover.
REVERSE_NUMBERING_SERIES = {
    "countdown",
    "countdown to final crisis",
    "zero hour",
    "zero hour: crisis in time"
}

# Centralized list of non-standard formats
NON_PLAIN_FORMATS = [
    'annual',
    'giant size',
    'giant-size',
    'graphic novel',
    'one shot',
    'one-shot',
    'hardcover',
    'trade paperback',
    'trade paper back',
    'tpb',
    'preview',
    'special'
]

# Ordered from LEAST restrictive to MOST restrictive
AGE_RATING_HIERARCHY = [
    "Early Childhood",
    "Everyone",
    "G",
    "Kids to Adults",
    "Everyone 10+",
    "PG",
    "Teen",
    "Rating Pending",
    "M",
    "MA15+",
    "Mature 17+",
    "Adults Only 18+",
    "R18+",
    "X18+"
]


@lru_cache(maxsize=128)
def _get_cached_rating_lists(max_age_rating: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """
    Internal cached worker.
    Input is a string (hashable), so lru_cache works perfectly.
    Returns TUPLES (Immutable) to prevent cache corruption.
    """
    try:
        max_index = AGE_RATING_HIERARCHY.index(max_age_rating)
        logger.debug(f"Calculated age config for rating: {max_age_rating}")
    except ValueError:
        max_index = -1

    allowed = tuple(AGE_RATING_HIERARCHY[:max_index + 1])
    banned = tuple(AGE_RATING_HIERARCHY[max_index + 1:])

    return allowed, banned

# Return type is now Tuple of Tuples (Immutable)
def get_age_rating_config(user) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """
    Public wrapper. Extracts the string from the user object and calls the cache.
    """
    if not user or user.is_superuser or not user.max_age_rating:
        logger.debug(f"User {user.username} has no age rating config or is superuser")
        return None, None

    # Pass ONLY the immutable string to the cached function
    return _get_cached_rating_lists(user.max_age_rating)

# --------------------


def is_comic_age_restricted_for_user(comic: Comic, user) -> bool:
    """Return True when the comic itself exceeds the user's age-rating access."""
    if not user or user.is_superuser or not user.max_age_rating:
        return False

    _, banned_ratings = get_age_rating_config(user)

    if comic.age_rating in banned_ratings:
        return True

    if not user.allow_unknown_age_ratings:
        if not comic.age_rating or comic.age_rating == "" or comic.age_rating.lower() == "unknown":
            return True

    return False


def assert_user_can_view_comic(comic: Comic, user, *, hide_denied: bool = False) -> None:
    """
    Enforce library and comic-level age access for a loaded Comic.

    `hide_denied=True` is intended for byte-serving endpoints so direct media
    URLs do not confirm whether a comic id exists.
    """
    denied_detail = "Comic not found" if hide_denied else "Content restricted by age rating"
    denied_status = 404 if hide_denied else 403

    if not user or not user.is_superuser:
        allowed_library_ids = {library.id for library in getattr(user, "accessible_libraries", [])}
        library_id = None
        if comic.volume and comic.volume.series:
            library_id = comic.volume.series.library_id

        if library_id not in allowed_library_ids:
            raise HTTPException(status_code=404, detail="Comic not found")

    if is_comic_age_restricted_for_user(comic, user):
        raise HTTPException(status_code=denied_status, detail=denied_detail)


def get_comic_age_restriction(user, comic_model=Comic):
    """
    Returns a SQLAlchemy BinaryExpression to filter COMIC rows directly.
    Used for: Search, Issue Lists, Cover Manifests.
    """
    if not user or user.is_superuser or not user.max_age_rating:
        return None

    allowed_ratings, banned_ratings = get_age_rating_config(user)

    # Logic:
    # 1. Matches an allowed rating
    # 2. OR (Matches Unknown AND user allows unknown)

    conditions = [comic_model.age_rating.in_(allowed_ratings)]

    if user.allow_unknown_age_ratings:
        # Allow NULL, Empty String, "Unknown" (case-insensitive), or ratings NOT in our official hierarchy
        # Note: We assume anything NOT in the banned list is okay if unknowns are allowed?
        # Safer: Explicitly check for null/empty/"Unknown"
        conditions.append(or_(
            comic_model.age_rating == None,
            comic_model.age_rating == "",
            func.lower(comic_model.age_rating) == "unknown"
        ))

    return or_(*conditions)


def get_series_age_restriction(user, series_model=Series):
    """
    Returns a SQLAlchemy BinaryExpression to filter SERIES rows.
    Implements 'Poison Pill' logic: Exclude series where ANY nested comic is banned.
    """
    if not user or user.is_superuser or not user.max_age_rating:
        return None

    allowed_ratings, banned_ratings = get_age_rating_config(user)

    # 1. Define what constitutes a "Banned Comic"
    # It has a banned rating
    banned_condition = Comic.age_rating.in_(banned_ratings)

    # If user does NOT allow unknowns, then Unknowns are also "Banned"
    if not user.allow_unknown_age_ratings:
        banned_condition = or_(
            banned_condition,
            Comic.age_rating == None,
            Comic.age_rating == "",
            func.lower(Comic.age_rating) == "unknown"
        )

    # 2. Filter Series that have ANY volume with ANY comic matching the banned condition
    # We use not_() and .any()
    # "Show me Series where NOT(Has Any Banned Comic)"
    return not_(series_model.volumes.any(Volume.comics.any(banned_condition)))

def get_banned_comic_condition(user):
    """
    Returns a SQLAlchemy BinaryExpression representing 'Banned Content' for this user.
    Used for filtering Lists and Collections.
    """
    if not user or user.is_superuser or not user.max_age_rating:
        return None

    allowed_ratings, banned_ratings = get_age_rating_config(user)

    # 1. Matches explicit ban list
    condition = Comic.age_rating.in_(banned_ratings)

    # 2. Matches Unknowns (if disallowed)
    if not user.allow_unknown_age_ratings:
        condition = or_(
            condition,
            Comic.age_rating == None,
            Comic.age_rating == "",
            func.lower(Comic.age_rating) == "unknown"
        )

    return condition


def check_container_restriction(db, user, item_model, fk_column, container_id: int, type_name: str):
    """
    Generic 'Fail Fast' security check for Collections and Reading Lists.
    Raises HTTPException(403) if the container holds ANY banned content.
    Args:
        db: Session
        user: CurrentUser
        item_model: The junction table (CollectionItem or ReadingListItem)
        fk_column: The column to filter (CollectionItem.collection_id)
        container_id: The ID to check
        type_name: "Collection" or "Reading list" (for error message)
    """
    if not user or user.is_superuser or not user.max_age_rating:
        return

    banned_condition = get_banned_comic_condition(user)
    if banned_condition is None:
        return

    # Check if ANY item in this container matches the ban
    has_banned = db.query(item_model.id).join(Comic)\
        .filter(fk_column == container_id)\
        .filter(banned_condition)\
        .first()

    if has_banned:
        raise HTTPException(status_code=403, detail=f"{type_name} contains age-restricted content")



def normalize_issue_number(number: str) -> str:
    """Normalize weird comic numbers (e.g. the '½' / '1/2' half-issue glyph)."""
    if not number:
        return number

    if number == "½" or number == "1/2":
        return "0.5"

    return number


def get_format_filters():
    """
    Returns SQL expressions to categorize comics.
    Usage: is_plain, is_annual, is_special = get_format_filters()
    """
    is_plain = or_(
        Comic.format == None,
        func.lower(Comic.format).not_in(NON_PLAIN_FORMATS)
    )

    is_annual = func.lower(Comic.format) == 'annual'

    is_special = (func.lower(Comic.format) != 'annual') & \
                 (func.lower(Comic.format).in_(NON_PLAIN_FORMATS))

    return is_plain, is_annual, is_special


def get_smart_cover(base_query, series_name: str = None, prefer_volume_order: bool = False):
    """
    Given a base query (filtered by series or volume), find the best cover.
    Priority:
    1. Plain Issue (not Annual/Special) AND Not Issue #0
    2. Fallback: First issue by Number/Date

    Args:
        base_query: The SQLAlchemy query object
        series_name: Optional name to trigger "Gimmick Detection" for reverse numbering.
        prefer_volume_order: Sort by volume number first for full-series cover selection.
    """
    is_plain, _, _ = get_format_filters()

    # Define Sort Logic
    sort_year = case((or_(Comic.year == None, Comic.year == -1), 9999), else_=Comic.year)
    sort_month = case((or_(Comic.month == None, Comic.month == -1), 99), else_=Comic.month)
    sort_day = case((or_(Comic.day == None, Comic.day == -1), 99), else_=Comic.day)

    # GIMMICK DETECTION
    # If this is a known reverse-numbering series, we want the HIGHEST number
    # (e.g., #51 or #4) to be the cover, not the lowest (#1 or #0).
    is_reverse_series = bool(series_name and series_name.lower() in REVERSE_NUMBERING_SERIES)
    number_order = get_issue_number_sort_keys(is_reverse_series)
    sort_order = (
        *number_order,
        sort_year.asc(),
        sort_month.asc(),
        sort_day.asc(),
    )
    if prefer_volume_order:
        sort_order = (Volume.volume_number.asc(), *sort_order)

    # PHASE 1: Strict "Best Cover" Search
    query = base_query.filter(is_plain) \
        .filter(Comic.number != '0') \
        .filter(not_(Comic.number.like('-%'))) \
        .filter(not_(Comic.number.like('%.5'))) \
        .order_by(*sort_order)

    cover = query.first()
    if cover:
        return cover

    # PHASE 2: Fallback
    return base_query.order_by(*sort_order).first()


def get_reading_time(total_pages):

    # Calculate Reading Time
    # Heuristic: 1.25 minutes per page
    total_minutes = int(total_pages * 1.25)

    if total_minutes >= 60:
        hours = total_minutes // 60
        minutes = total_minutes % 60
        read_time = f"{hours}h {minutes}m"
    else:
        read_time = f"{total_minutes}m"

    return read_time


def get_resume_target(
        db: SessionDep,
        *,
        user_id: int,
        series_name: str | None,
        first_issue_id: int | None,
        series_id: int | None = None,
        volume_id: int | None = None,
) -> tuple[int | None, str]:
    """
    Resolve the best "read next" target for a series or volume detail page.

    Semantics:
    - If the latest touched comic is still in progress, keep "Continue" behavior.
    - If the latest touched comic is completed, advance to the next unread comic
      but keep the container-level "Continue" behavior.
    - Fall back to the first issue when no progress exists or the run is exhausted.
    """
    if (series_id is None) == (volume_id is None):
        raise ValueError("Exactly one of series_id or volume_id must be provided")

    progress_query = db.query(ReadingProgress).join(Comic)
    comics_query = db.query(Comic)

    if series_id is not None:
        progress_query = progress_query.join(Volume).filter(Volume.series_id == series_id)
        comics_query = comics_query.join(Volume).filter(Volume.series_id == series_id)
    else:
        progress_query = progress_query.filter(Comic.volume_id == volume_id)
        comics_query = comics_query.filter(Comic.volume_id == volume_id)

    latest_progress = (
        progress_query
        .filter(ReadingProgress.user_id == user_id)
        .order_by(ReadingProgress.last_read_at.desc())
        .first()
    )

    if not latest_progress:
        return first_issue_id, "new"

    if not latest_progress.completed and (latest_progress.current_page or 0) > 0:
        return latest_progress.comic_id, "in_progress"

    completed_ids = {
        row[0]
        for row in (
            progress_query
            .with_entities(ReadingProgress.comic_id)
            .filter(
                ReadingProgress.user_id == user_id,
                ReadingProgress.completed == True,
            )
            .all()
        )
    }

    is_reverse_series = bool(series_name and series_name.lower() in REVERSE_NUMBERING_SERIES)
    number_order = get_issue_number_sort_keys(is_reverse_series)

    if series_id is not None:
        ordered_comics = comics_query.order_by(
            Volume.volume_number.asc(),
            *number_order,
        ).all()
    else:
        ordered_comics = comics_query.order_by(
            *number_order,
        ).all()

    ordered_ids = [comic.id for comic in ordered_comics]
    try:
        start_index = ordered_ids.index(latest_progress.comic_id) + 1
    except ValueError:
        return first_issue_id, "new"

    for comic_id in ordered_ids[start_index:]:
        if comic_id not in completed_ids:
            return comic_id, "continue"

    return first_issue_id, "new"


# Helper for SQL Order By
def get_format_sort_index():
    """
    Returns a SQLAlchemy CASE expression to weight formats.
    Usage: query.order_by(get_format_sort_index(), ...)

    Weights:
    1: Plain Issues (Default)
    2: Annuals
    3: Specials / Other Non-Plain
    """
    return case(
        (func.lower(Comic.format) == 'annual', 2),
        (func.lower(Comic.format).in_(NON_PLAIN_FORMATS), 3),
        else_=1
    )


def get_issue_number_sort_keys(reverse_numbering: bool = False):
    """
    Return SQLAlchemy ordering expressions for issue-number-first comic runs.
    """
    sort_number = cast(Comic.number, Float)
    non_numeric_rank = case(
        (
            or_(
                Comic.number == None,
                Comic.number == "",
                (sort_number == 0) & not_(Comic.number.in_(("0", "0.0", "0.00"))),
            ),
            1,
        ),
        else_=0,
    )

    if reverse_numbering:
        return non_numeric_rank.asc(), sort_number.desc(), Comic.number.desc()

    return non_numeric_rank.asc(), sort_number.asc(), Comic.number.asc()


# Helper for Python Sorting
def get_format_weight(fmt_string: str) -> int:
    """
    Returns integer weight for python-side sorting.
    """
    if not fmt_string:
        return 1

    fmt = fmt_string.lower().strip()

    if fmt == 'annual':
        return 2
    if fmt in NON_PLAIN_FORMATS:
        return 3

    return 1


# Aggregation Helper
def get_aggregated_metadata(
        db: SessionDep,
        model,
        context_join_model,
        context_filter_col,
        context_id: int,
        role_filter: str = None,
        allowed_library_ids: list[int] = None
):
    """
    Generic helper to fetch distinct metadata (Writers, Characters, etc.)
    for a group of comics (Reading List, Collection, etc).

    Args:
        db: Database Session
        model: The target metadata model (Person, Character, Team)
        context_join_model: The junction table (ReadingListItem, CollectionItem)
        context_filter_col: The column to filter by (ReadingListItem.reading_list_id)
        context_id: The ID of the list/collection
        role_filter: Optional role for Credits (e.g. 'writer')
        allowed_library_ids: Optional list of library ids to include (e.g. [1, 2, 3])
    """
    query = db.query(model.name)

    # 1. Join Strategy based on Target Metadata Model
    if model == Person:
        # Person -> ComicCredit -> Comic
        query = query.join(ComicCredit).join(Comic)
        if role_filter:
            query = query.filter(ComicCredit.role == role_filter)
    else:
        # Tags (Many-to-Many relationships on Comic)
        # Note: We join FROM the tag TO the comic
        if model == Character:
            query = query.join(Comic.characters)
        elif model == Team:
            query = query.join(Comic.teams)
        elif model == Location:
            query = query.join(Comic.locations)
        elif model == Genre:
            query = query.join(Comic.genres)

    # 2. Join Context (The List/Collection Item table)
    # We join the context model to the Comic
    query = query.join(context_join_model, context_join_model.comic_id == Comic.id)

    # 3. Security Scope (Filter by Library)
    if allowed_library_ids is not None:
        # We need to join up to Series to check library_id
        # Note: Some paths (like Tags) join Comic directly, so we just need Comic -> Volume -> Series
        query = query.join(Volume, Comic.volume_id == Volume.id) \
            .join(Series, Volume.series_id == Series.id) \
            .filter(Series.library_id.in_(allowed_library_ids))

    # 4. Apply Filter
    query = query.filter(context_filter_col == context_id)

    return sorted([r[0] for r in query.distinct().all()])

def get_thumbnail_url(comic_id: int, updated_at: datetime) -> str:
    """Standardized thumbnail URL with cache-busting version string"""
    version = get_thumbnail_hash(updated_at)
    return f"/api/comics/{comic_id}/thumbnail?v={version}" # TODO: make relative url (no leading /) and let frontend decide base url

def get_thumbnail_hash(updated_at: datetime) -> int:
    version = int(updated_at.timestamp()) if updated_at else 0
    return version
