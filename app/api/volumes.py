from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, case, Float, Integer
from sqlalchemy.orm import joinedload

from typing import List, Annotated

from app.core.comic_helpers import get_format_filters, get_smart_cover, get_reading_time

from app.api.deps import SessionDep, CurrentUser, VolumeDep
from app.api.deps import PaginationParams, PaginatedResponse

from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.library import Library
from app.models.credits import Person, ComicCredit
from app.models.tags import Character, Team, Location
from app.models.reading_progress import ReadingProgress

router = APIRouter()

def comic_to_simple_dict(comic: Comic):
    return {
        "id": comic.id,
        "volume_number": comic.volume.volume_number,
        "number": comic.number,
        "title": comic.title,
        "year": comic.year,
        "format": comic.format,
        "filename": comic.filename,
        "thumbnail_path": f"/api/comics/{comic.id}/thumbnail" # TODO: make relative url (no leading /) and let frontend decide base url
    }


@router.get("/{volume_id}")
async def get_volume_detail(volume: VolumeDep, db: SessionDep, current_user: CurrentUser):
    """
    Get volume summary with categorized counts.
    """

    # Filters
    is_plain, is_annual, is_special = get_format_filters()

    # 1. Categorized Counts
    stats = db.query(
        func.count(case((is_plain, 1))).label('plain_count'),
        func.count(case((is_annual, 1))).label('annual_count'),
        func.count(case((is_special, 1))).label('special_count'),
        func.min(Comic.year).label('start_year'),
        func.max(Comic.year).label('end_year'),
        func.max(Comic.publisher).label('publisher'),
        func.max(Comic.imprint).label('imprint'),
        func.max(Comic.count).label('max_count'),  # Get the highest 'Count' value found
        func.sum(Comic.page_count).label('total_pages')
    ).filter(Comic.volume_id == volume.id).first()

    # Calculate Reading Time
    total_pages = stats.total_pages or 0
    read_time = get_reading_time(total_pages)

    # Story Arc Aggregation (Scoped to Volume)
    # 1. Fetch all issues in this volume that have a story_arc defined
    # 2. Sort by Number so we can identify the "First Issue" of the arc
    arc_rows = db.query(Comic.id, Comic.story_arc, Comic.number) \
        .filter(Comic.volume_id == volume.id) \
        .filter(Comic.story_arc != None, Comic.story_arc != "") \
        .order_by(func.cast(Comic.number, Float), Comic.number) \
        .all()

    # Group by Arc Name
    story_arcs_map = {}
    for row in arc_rows:
        name = row.story_arc
        if name not in story_arcs_map:
            story_arcs_map[name] = {
                "name": name,
                "first_issue_id": row.id,  # First one encountered is the thumbnail/link
                "count": 0
            }
        story_arcs_map[name]["count"] += 1

    # Convert to list and sort alphabetically by Arc Name
    story_arcs_data = sorted(story_arcs_map.values(), key=lambda x: x['name'])


    # 2. Find Cover (Plain issues priority)
    base_query = db.query(Comic).filter(Comic.volume_id == volume.id)
    first_issue = get_smart_cover(base_query)

    # Get colors from the cover of the 1st issue comic
    colors = {"primary": "#000000", "secondary": "#222222"}
    if first_issue:
        colors["primary"] = first_issue.color_primary or "#000000"
        colors["secondary"] = first_issue.color_secondary or "#222222"

    # Resume Logic
    resume_comic_id = None
    read_status = "new"

    last_read = db.query(ReadingProgress).join(Comic) \
        .filter(Comic.volume_id == volume.id) \
        .filter(ReadingProgress.user_id == current_user.id) \
        .order_by(ReadingProgress.last_read_at.desc()) \
        .first()

    if last_read:
        resume_comic_id = last_read.comic_id
        read_status = "in_progress"
    elif first_issue:
        resume_comic_id = first_issue.id

    # Status & Missing Issues Calculation
    status = "ongoing"
    missing_issues = []
    is_completed = False
    expected_count = stats.max_count  # only calculate "Missing" if we have a valid Count > 0

    # Logic: Is this a Standalone Volume?
    # No plain issues, but has Annuals or Specials.
    is_standalone = (stats.plain_count == 0 and (stats.annual_count > 0 or stats.special_count > 0))

    if is_standalone:
        # Case A: Standalone (Graphic Novel, TPB, One-Shot)
        # Even if metadata says Count=1, we don't treat this as a missing "Issue #1".
        # We assume if the standalone volume exists, it is complete.
        status = "ended"
        is_completed = True
        missing_issues = []

    elif expected_count and expected_count > 0:
        # Case B: Standard Numbered Series
        status = "ended"

        # Fetch all existing "Plain" issue numbers for this volume
        # We cast to Integer to ensure we are comparing numbers (1 vs 01)
        # Note: This ignores ".5" or "10a" variants for the completion check,
        # which is standard behavior for "Count" logic.
        existing_numbers = db.query(func.cast(Comic.number, Integer)) \
            .filter(Comic.volume_id == volume.id) \
            .filter(is_plain) \
            .all()

        # Create sets for comparison
        existing_set = set(row[0] for row in existing_numbers if row[0] is not None)
        expected_set = set(range(1, expected_count + 1))

        # Find the difference
        missing_set = expected_set - existing_set

        if not missing_set:
            is_completed = True
        else:
            # Sort the missing numbers for display (e.g., [2, 3, 4])
            missing_issues = sorted(list(missing_set))


    # 3. Aggregated Metadata (Scoped ONLY to this volume)
    writers = db.query(Person.name).join(ComicCredit).join(Comic) \
        .filter(Comic.volume_id == volume.id).filter(ComicCredit.role == 'writer').distinct().all()

    pencillers = db.query(Person.name).join(ComicCredit).join(Comic) \
        .filter(Comic.volume_id == volume.id).filter(ComicCredit.role == 'penciller').distinct().all()

    characters = db.query(Character.name).join(Comic.characters) \
        .filter(Comic.volume_id == volume.id).distinct().all()

    teams = db.query(Team.name).join(Comic.teams) \
        .filter(Comic.volume_id == volume.id).distinct().all()

    locations = db.query(Location.name).join(Comic.locations) \
        .filter(Comic.volume_id == volume.id).distinct().all()

    return {
        "id": volume.id,
        "volume_number": volume.volume_number,
        "series_id": volume.series.id,
        "series_name": volume.series.name,
        "library_id": volume.series.library_id,
        "library_name": volume.series.library.name,

        # Counts
        "total_issues": stats.plain_count,  # Use plain count as main count
        "annual_count": stats.annual_count,
        "special_count": stats.special_count,
        "total_pages": total_pages,
        "read_time": read_time,

        # Status Fields
        "status": status,  # "Ongoing" or "Ended"
        "expected_count": expected_count,  # e.g. 12
        "is_completed": is_completed,  # True if you have 1..12
        "missing_issues": missing_issues,  # e.g. [5, 6] or []
        "is_standalone": is_standalone,

        "publisher": stats.publisher,
        "imprint": stats.imprint,
        "start_year": stats.start_year,
        "end_year": stats.end_year,
        "first_issue_id": first_issue.id if first_issue else None,
        "story_arcs": story_arcs_data,
        "details": {
            "writers": sorted([r[0] for r in writers]),
            "pencillers": sorted([r[0] for r in pencillers]),
            "characters": sorted([r[0] for r in characters]),
            "teams": sorted([r[0] for r in teams]),
            "locations": sorted([r[0] for r in locations])
        },
        "resume_to": {
            "comic_id": resume_comic_id,
            "status": read_status
        },
        "colors": colors,
    }

@router.get("/{volume_id}/issues", response_model=PaginatedResponse)
async def get_volume_issues(
        current_user: CurrentUser,
        volume_id: int,
        params: Annotated[PaginationParams, Depends()],
        db: SessionDep,
        type: Annotated[str, Query(pattern="^(plain|annual|special|all)$")] = "plain"
):
    """
    Get paginated issues for a specific volume, filtered by type.
    """

    # Verify Volume Access First
    # We must ensure the volume belongs to a visible series
    access_check = db.query(Volume).join(Series).filter(Volume.id == volume_id)

    if not current_user.is_superuser:
        allowed_ids = [lib.id for lib in current_user.accessible_libraries]
        access_check = access_check.filter(Series.library_id.in_(allowed_ids))

    if not access_check.first():
        raise HTTPException(status_code=404, detail="Volume not found")

    # Select Comic AND the completed status
    query = db.query(Comic, ReadingProgress.completed).outerjoin(
        ReadingProgress,
        (ReadingProgress.comic_id == Comic.id) & (ReadingProgress.user_id == current_user.id)
    ).filter(Comic.volume_id == volume_id)


    is_plain, is_annual, is_special = get_format_filters()

    # Apply Filters
    if type == "plain":
        query = query.filter(is_plain)
    elif type == "annual":
        query = query.filter(is_annual)
    elif type == "special":
        query = query.filter(is_special)

    total = query.count()

    # Sort by Numeric Value first, then String Value for variants (10a, 10b)
    # Cast number to Float for correct numeric sorting
    # Volume number is already int, so it sorts fine.
    comics = query.order_by(func.cast(Comic.number, Float), Comic.number) \
        .offset(params.skip) \
        .limit(params.size) \
        .all()

    # Map results
    # Unpack the tuple (Comic, completed)
    items = []
    for comic, is_completed in comics:
        data = comic_to_simple_dict(comic)
        # If is_completed is None (no record) or False, it's unread
        data['read'] = True if is_completed else False
        items.append(data)


    return {
        "total": total,
        "page": params.page,
        "size": params.size,
        "items": items
    }