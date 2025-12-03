from sqlalchemy import func, or_, not_, case

from app.api.deps import SessionDep
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.tags import Character, Team, Location, Genre
from app.models.credits import Person, ComicCredit

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


def get_smart_cover(base_query):
    """
    Given a base query (filtered by series or volume), find the best cover.
    Priority:
    1. Plain Issue (not Annual/Special) AND Not Issue #0
    2. Fallback: First issue by Year/Number
    """
    is_plain, _, _ = get_format_filters()

    # 1. Try to find a *positive* standard issue (No #-1, #0, No Annuals)
    cover = base_query.filter(is_plain) \
        .filter(Comic.number != '0') \
        .filter(not_(Comic.number.like('-%'))) \
        .order_by(Comic.year, Comic.number) \
        .first()

    if cover:
        return cover

    # 2. Fallback: Just give me the first thing you have (Annuals, #0, etc)
    return base_query.order_by(Comic.year, Comic.number).first()

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
