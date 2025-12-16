from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, case, Float, Integer, literal, or_
from sqlalchemy.orm import joinedload

from typing import List, Annotated

from app.core.comic_helpers import (get_format_filters, get_smart_cover, get_reading_time,
                                    REVERSE_NUMBERING_SERIES, get_age_rating_config)

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


@router.get("/{volume_id}", name="detail")
async def get_volume_detail(volume: VolumeDep, db: SessionDep, current_user: CurrentUser):
    """
    Get volume summary with categorized counts.
    OPTIMIZED: Uses UNION ALL to fetch all metadata lists (writers, characters, etc) in 1 query.
    """

    # Note: VolumeDep handles 404, but we need to check restrictions.
    # 0. Check Age Restriction: Poison Pill check
    # If the user has restrictions, we check if this volume contains ANY banned content.
    if not current_user.is_superuser and current_user.max_age_rating:

        allowed_ratings, banned_ratings = get_age_rating_config(current_user)

        # Build the "Banned" filter
        ban_conditions = [Comic.age_rating.in_(banned_ratings)]

        # If user explicitly disallows Unknowns, treat them as banned
        if not current_user.allow_unknown_age_ratings:
            ban_conditions.append(or_(
                Comic.age_rating == None,
                Comic.age_rating == "",
                func.lower(Comic.age_rating) == "unknown"
            ))

        # Run the check: Does a banned comic exist in this volume?
        has_banned_content = db.query(Comic.id).filter(
            Comic.volume_id == volume.id,
            or_(*ban_conditions)
        ).first()

        if has_banned_content:
            raise HTTPException(status_code=403, detail="Volume contains age-restricted content")
    # --------------------------------------



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

        # Only look at 'Count' if the issue is a standard (Plain) issue.
        # This ignores "1 of 1" tags on Specials/One-shots.
        func.max(case((is_plain, Comic.count))).label('max_count'),

        func.sum(Comic.page_count).label('total_pages'),
        func.sum(Comic.file_size).label('total_size')
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
    first_issue = get_smart_cover(base_query, series_name=volume.series.name)

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

    # 5. Status & Missing Issues Logic
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

        # What we have
        existing_set = set(row[0] for row in existing_numbers if row[0] is not None)

        # Detect if this series uses "Zero Indexing" (Starts at #0)
        # Note: Since existing_numbers is filtered by 'is_plain', a Special #0 won't trigger this.
        # This correctly forces the user to tag #0 as 'Plain' if it counts towards the run.
        has_zero_issue = 0 in existing_set
        if has_zero_issue:
            # If we have a #0, the range is 0 to (Count - 1)
            # Example: Count 4 becomes {0, 1, 2, 3}
            expected_set = set(range(0, expected_count))
        else:
            # Standard 1-based indexing
            # Example: Count 4 becomes {1, 2, 3, 4}
            expected_set = set(range(1, expected_count + 1))

        # Find the difference
        missing_set = expected_set - existing_set

        if not missing_set:
            is_completed = True
        else:
            # Sort the missing numbers for display (e.g., [2, 3, 4])
            missing_issues = sorted(list(missing_set))

    # 6. Aggregated Metadata (OPTIMIZED)
    # Instead of 5 separate queries, we construct a UNION ALL to get everything in 1 round trip.

    # Define sub-selectors
    # Note: literal() allows us to tag the rows so we can sort them in Python
    q_writers = db.query(Person.name.label("name"), literal("writer").label("type")) \
        .join(ComicCredit).join(Comic).filter(Comic.volume_id == volume.id).filter(ComicCredit.role == 'writer')

    q_pencillers = db.query(Person.name.label("name"), literal("penciller").label("type")) \
        .join(ComicCredit).join(Comic).filter(Comic.volume_id == volume.id).filter(ComicCredit.role == 'penciller')

    q_chars = db.query(Character.name.label("name"), literal("character").label("type")) \
        .join(Comic.characters).filter(Comic.volume_id == volume.id)

    q_teams = db.query(Team.name.label("name"), literal("team").label("type")) \
        .join(Comic.teams).filter(Comic.volume_id == volume.id)

    q_locs = db.query(Location.name.label("name"), literal("location").label("type")) \
        .join(Comic.locations).filter(Comic.volume_id == volume.id)

    # Union and execute
    # distinct() handles duplicates within the union logic
    meta_rows = q_writers.union_all(q_pencillers, q_chars, q_teams, q_locs).distinct().all()

    # Buckets
    details = {
        "writers": [],
        "pencillers": [],
        "characters": [],
        "teams": [],
        "locations": []
    }

    # Python sort/group
    for name, type_tag in meta_rows:
        if type_tag == "writer":
            details["writers"].append(name)
        elif type_tag == "penciller":
            details["pencillers"].append(name)
        elif type_tag == "character":
            details["characters"].append(name)
        elif type_tag == "team":
            details["teams"].append(name)
        elif type_tag == "location":
            details["locations"].append(name)

    # Final sort
    for k in details:
        details[k].sort()

    # Calculate Gimmick Flag
    is_reverse_series = False
    if volume.series:
        is_reverse_series = volume.series.name.lower() in REVERSE_NUMBERING_SERIES

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
        "file_size": stats.total_size or 0,

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
        "details": details,
        "resume_to": {
            "comic_id": resume_comic_id,
            "status": read_status
        },
        "colors": colors,
        "is_reverse_numbering": is_reverse_series,
    }


@router.get("/{volume_id}/issues", response_model=PaginatedResponse, name="issues")
async def get_volume_issues(
        current_user: CurrentUser,
        volume_id: int,
        params: Annotated[PaginationParams, Depends()],
        db: SessionDep,
        type: Annotated[str, Query(pattern="^(plain|annual|special|all)$")] = "plain",
        read_filter: Annotated[str, Query(pattern="^(all|read|unread)$")] = "all",
        sort_order: Annotated[str, Query(pattern="^(asc|desc)$")] = "asc"
):
    """
    Get paginated issues for a specific volume.
    OPTIMIZED: Eager loads Comic.volume to prevent N+1 in serializer.
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
    # OPTIMIZATION: joinedload(Comic.volume) prevents N+1 when accessing volume_number
    query = db.query(Comic, ReadingProgress.completed).outerjoin(
        ReadingProgress,
        (ReadingProgress.comic_id == Comic.id) & (ReadingProgress.user_id == current_user.id)
    ).options(joinedload(Comic.volume)) \
        .filter(Comic.volume_id == volume_id)


    # --- AGE RATING FILTER ---
    # TODO: If partial views are ever implemented we can uncomment this check
    # Even if the Volume is allowed, we double-check individual issues (defensive coding)
    # or just rely on the Volume check?
    # Logic: If the volume is allowed, technically all comics are allowed (Poison Pill).
    # BUT: If we change logic later to "Partial View", this line saves us.
    #age_filter = get_comic_age_restriction(current_user)
    #if age_filter is not None:
    #    query = query.filter(age_filter)
    # -------------------------

    is_plain, is_annual, is_special = get_format_filters()

    # Type Filters
    if type == "plain":
        query = query.filter(is_plain)
    elif type == "annual":
        query = query.filter(is_annual)
    elif type == "special":
        query = query.filter(is_special)

    # Read Status Filter
    if read_filter == "read":
        query = query.filter(ReadingProgress.completed == True)
    elif read_filter == "unread":
        query = query.filter(
            (ReadingProgress.completed == None) |
            (ReadingProgress.completed == False)
        )

    # Smart Sorting Strategy
    # We define the 2-stage sort keys:
    # 1. Numeric Value (9 before 10)
    # 2. String Value (10a before 10b)
    sort_keys = [func.cast(Comic.number, Float), Comic.number]

    if sort_order == "desc":
        query = query.order_by(*[k.desc() for k in sort_keys])
    else:
        query = query.order_by(*[k.asc() for k in sort_keys])

    # Pagination & Execute
    total = query.count()
    comics = query.offset(params.skip).limit(params.size).all()

    # Map results
    # Unpack the tuple (Comic, completed)
    items = []
    for comic, is_completed in comics:
        # Now efficient because comic.volume is loaded
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
