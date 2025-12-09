from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy import func, case, Float
from sqlalchemy.orm import joinedload
from typing import List, Optional, Annotated
from datetime import datetime, timezone

from app.core.comic_helpers import get_format_filters, get_smart_cover, get_reading_time

from app.api.deps import SessionDep, CurrentUser, AdminUser, SeriesDep
from app.api.deps import PaginationParams, PaginatedResponse

# Import related models
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.collection import Collection, CollectionItem
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.credits import Person, ComicCredit
from app.models.tags import Character, Team, Location, Genre, comic_genres
from app.models.interactions import UserSeries
from app.models.reading_progress import ReadingProgress

from app.services.thumbnailer import ThumbnailService

router = APIRouter()

def comic_to_simple_dict(comic: Comic):
    """Lightweight dict for list views"""
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

# Helper to serialize Series
def series_to_simple_dict(series, db, current_user):
    """
        Helper to serialize Series for the card UI.
        Fetches the 'Smart Cover' to get the thumbnail and YEAR.
    """

    is_fully_read = False
    if current_user:
        counts = db.query(
            func.count(Comic.id).label('total'),
            func.count(case((ReadingProgress.completed == True, 1))).label('read')
        ).select_from(Comic).outerjoin(
            ReadingProgress,
            (ReadingProgress.comic_id == Comic.id) & (ReadingProgress.user_id == current_user.id)
        ).join(Volume).filter(Volume.series_id == series.id).first()

        is_fully_read = (counts.total > 0) and (counts.read >= counts.total)


    # This is a bit N+1, ideally we optimize or use a subquery,
    # but for 10-20 items it's acceptable for v1

    # 1. Construct query for comics in this series
    base_query = db.query(Comic).join(Volume).filter(Volume.series_id == series.id)

    # 2. Use smart cover (usually Issue #1) to get the representative Year and Thumbnail
    thumb_comic = get_smart_cover(base_query)

    # 3. Fallback if no smart cover found
    if not thumb_comic:
        thumb_comic = base_query.first()

    return {
        "id": series.id,
        "name": series.name,
        "start_year": thumb_comic.year if thumb_comic else None,
        "thumbnail_path": f"/api/comics/{thumb_comic.id}/thumbnail" if thumb_comic else None, # TODO: make relative url (no leading /)
        "read": is_fully_read
    }

@router.get("/{series_id}", name="detail")
async def get_series_detail(series: SeriesDep, db: SessionDep, current_user: CurrentUser):
    """
    Get series summary including Related content and Metadata Details.
    """

    # 1. Get Volumes (sorted by volume_number)
    volumes = db.query(Volume).filter(Volume.series_id == series.id).order_by(Volume.volume_number).all()
    volume_ids = [v.id for v in volumes]

    if not volume_ids:
        return {
            "id": series.id,
            "name": series.name,
            "library_id": series.library_id,
            "volume_count": 0,
            "total_issues": 0,
            "volumes": [],
            "collections": [],
            "reading_lists": [],
            "details": {}
        }

    # Get centralized filters
    is_plain, is_annual, is_special = get_format_filters()

    # 2. Aggregation Stats (Counts)
    stats = db.query(
        func.count(case((is_plain, 1))).label('plain_count'),
        func.count(case((is_annual, 1))).label('annual_count'),
        func.count(case((is_special, 1))).label('special_count'),
        func.min(Comic.year).label('start_year'),
        func.max(Comic.publisher).label('publisher'),
        func.max(Comic.imprint).label('imprint'),
        func.sum(Comic.page_count).label('total_pages'),
        func.sum(Comic.file_size).label('total_size')
    ).filter(Comic.volume_id.in_(volume_ids)).first()

    # Calculate Reading Time
    total_pages = stats.total_pages or 0
    read_time = get_reading_time(total_pages)

    # Logic: Is this a Standalone Series?
    # No plain issues, but has Annuals or Specials.
    is_standalone = (stats.plain_count == 0 and (stats.annual_count > 0 or stats.special_count > 0))

    # [NEW] Story Arc Aggregation
    # We fetch enough data to Sort and Group
    # Note: We use the same sorting logic as the main list to ensure "First" is actually "First"
    arc_issues = db.query(Comic.id, Comic.story_arc, Comic.number, Volume.volume_number) \
        .join(Volume) \
        .filter(Comic.volume_id.in_(volume_ids)) \
        .filter(Comic.story_arc != None, Comic.story_arc != "") \
        .order_by(Volume.volume_number, func.cast(Comic.number, Float), Comic.number) \
        .all()

    # Process in Python
    # Since we sorted via SQL, the first time we encounter an Arc, it is the first issue.
    story_arcs_map = {}

    for row in arc_issues:
        name = row.story_arc
        if name not in story_arcs_map:
            story_arcs_map[name] = {
                "name": name,
                "first_issue_id": row.id,  # This is the entry point for the Reader
                "count": 0
            }
        story_arcs_map[name]["count"] += 1

    # Convert to list and sort alphabetically by Arc Name
    story_arcs_data = sorted(story_arcs_map.values(), key=lambda x: x['name'])

    # 3. Related Content & Metadata (Collections, Reading Lists, Credits)
    related_collections = db.query(Collection).join(CollectionItem).join(Comic).filter(
        Comic.volume_id.in_(volume_ids)).distinct().all()
    related_reading_lists = db.query(ReadingList).join(ReadingListItem).join(Comic).filter(
        Comic.volume_id.in_(volume_ids)).distinct().all()

    writers = db.query(Person.name).join(ComicCredit).join(Comic).filter(Comic.volume_id.in_(volume_ids)).filter(
        ComicCredit.role == 'writer').distinct().all()
    pencillers = db.query(Person.name).join(ComicCredit).join(Comic).filter(Comic.volume_id.in_(volume_ids)).filter(
        ComicCredit.role == 'penciller').distinct().all()
    characters = db.query(Character.name).join(Comic.characters).filter(
        Comic.volume_id.in_(volume_ids)).distinct().all()
    teams = db.query(Team.name).join(Comic.teams).filter(Comic.volume_id.in_(volume_ids)).distinct().all()
    locations = db.query(Location.name).join(Comic.locations).filter(Comic.volume_id.in_(volume_ids)).distinct().all()

    # 4. SERIES COVER Logic
    # Priority: Plain issue, not #0, sorted by year/number
    base_query = db.query(Comic).filter(Comic.volume_id.in_(volume_ids))
    first_issue = get_smart_cover(base_query)

    # Get colors from the cover of the 1st issue comic
    colors = {}
    if first_issue:
        colors = first_issue.color_palette or {}

    # Resume Logic
    resume_comic_id = None
    read_status = "new"

    # Check for last read comic in this series
    last_read = db.query(ReadingProgress).join(Comic).join(Volume) \
        .filter(Volume.series_id == series.id) \
        .filter(ReadingProgress.user_id == current_user.id) \
        .order_by(ReadingProgress.last_read_at.desc()) \
        .first()

    if last_read:
        resume_comic_id = last_read.comic_id
        read_status = "in_progress"
    elif first_issue:
        # Fallback to the first issue (calculated for the cover)
        resume_comic_id = first_issue.id

    # 5. VOLUMES DATA Loop
    volumes_data = []
    for vol in volumes:
        count = db.query(Comic).filter(Comic.volume_id == vol.id).count()

        # Scoped query for this volume
        vol_base_query = db.query(Comic).filter(Comic.volume_id == vol.id)
        vol_first = get_smart_cover(vol_base_query)

        # Count how many issues in this volume are marked 'completed' by the user
        read_count = db.query(ReadingProgress).join(Comic).filter(
            Comic.volume_id == vol.id,
            ReadingProgress.user_id == current_user.id,
            ReadingProgress.completed == True
        ).count()

        # It is "Read" only if not empty AND read count matches total count
        is_fully_read = (count > 0) and (read_count >= count)

        volumes_data.append({
            "volume_id": vol.id,
            "volume_number": vol.volume_number,
            "first_issue_id": vol_first.id if vol_first else None,
            "issue_count": count,
            "read": is_fully_read,
        })

    # Check if starred
    is_starred = False
    if current_user:
        pref = db.query(UserSeries).filter(
            UserSeries.user_id == current_user.id,
            UserSeries.series_id == series.id
        ).first()
        if pref and pref.is_starred:
            is_starred = True


    return {
        "id": series.id,
        "name": series.name,
        "library_id": series.library_id,
        "library_name": series.library.name,
        "publisher": stats.publisher,
        "imprint": stats.imprint,
        "start_year": stats.start_year,
        "volume_count": len(volumes),
        "total_issues": stats.plain_count,
        "annual_count": stats.annual_count,
        "special_count": stats.special_count,
        "is_standalone": is_standalone,
        "total_pages": total_pages,
        "file_size": stats.total_size or 0,
        "read_time": read_time,
        "starred": is_starred,
        "first_issue_id": first_issue.id if first_issue else None,
        "volumes": volumes_data,
        "collections": [{"id": c.id, "name": c.name, "description": c.description} for c in related_collections],
        "reading_lists": [{"id": l.id, "name": l.name, "description": l.description} for l in related_reading_lists],
        "story_arcs": sorted(story_arcs_data, key=lambda x: x['name']),
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
        "is_admin": current_user.is_superuser
    }


@router.get("/{series_id}/issues", response_model=PaginatedResponse, name="issues")
async def get_series_issues(
        current_user: CurrentUser,
        series_id: int,
        params: Annotated[PaginationParams, Depends()],
        db: SessionDep,
        type: Annotated[str, Query(pattern="^(plain|annual|special|all)$")] = "plain"
):
    """
    Get paginated issues for a series, filtered by type.
    """
    # Select Comic AND the completed status
    query = db.query(Comic, ReadingProgress.completed).outerjoin(
        ReadingProgress,
        (ReadingProgress.comic_id == Comic.id) & (ReadingProgress.user_id == current_user.id)
    ).join(Volume).join(Series).filter(Series.id == series_id)


    # Get filters
    is_plain, is_annual, is_special = get_format_filters()

    if type == "plain":
        query = query.filter(is_plain)
    elif type == "annual":
        query = query.filter(is_annual)
    elif type == "special":
        query = query.filter(is_special)

    total = query.count()

    # Sort by Numeric Value first, then String Value for variants (10a, 10b)
    # Cast number to Float for correct numeric sorting (1, 2, 10 instead of 1, 10, 2)
    # Volume number is already int, so it sorts fine.
    comics = query.order_by(Volume.volume_number, func.cast(Comic.number, Float), Comic.number) \
        .offset(params.skip) \
        .limit(params.size) \
        .all()

    # Map results
    # We unpack the tuple (Comic, completed)
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


@router.get("/", response_model=PaginatedResponse, name="list")
async def list_series(
        db: SessionDep,
        current_user: CurrentUser,
        params: Annotated[PaginationParams, Depends()],
        only_starred: bool = False,
        sort_by: Annotated[str, Query(pattern="^(name|created|updated)$")] = "name",
        sort_desc: bool = False

):
    """
    List series with sorting and user-access filtering.
    Used for Home Page 'Recently Added' sliders.
    """
    query = db.query(Series)

    # 1. Apply Security Filter (unless Superuser)
    if not current_user.is_superuser:
        # Join Library to check permissions
        # Filter where Series.library_id is in user.accessible_libraries
        allowed_ids = [lib.id for lib in current_user.accessible_libraries]
        query = query.filter(Series.library_id.in_(allowed_ids))

    # Filter Starred
    if only_starred:
        query = query.join(UserSeries).filter(
            UserSeries.user_id == current_user.id,
            UserSeries.is_starred == True
        )

    # 2. Apply Sorting
    if sort_by == "created":
        sort_col = Series.created_at
    elif sort_by == "updated":
        sort_col = Series.updated_at
    else:
        # Smart Sort here too
        sort_col = case(
            (Series.name.ilike("The %"), func.substr(Series.name, 5)),
            else_=Series.name
        )

    if sort_desc:
        query = query.order_by(sort_col.desc())
    else:
        query = query.order_by(sort_col.asc())

    # 3. Pagination
    total = query.count()
    series_list = query.offset(params.skip).limit(params.size).all()

    # 4. Format Results (Need thumbnails)
    items = []
    for s in series_list:
        # Find a cover image (First issue of first volume)
        # Optimization: In a real large scale app, this should be denormalized or eager loaded
        base_query = db.query(Comic).join(Volume).filter(Volume.series_id == s.id)
        first_issue = get_smart_cover(base_query)

        # Check if ANY issue in this series is read
        has_read_any = db.query(ReadingProgress).join(Comic).join(Volume).filter(
            Volume.series_id == s.id,
            ReadingProgress.user_id == current_user.id,
            ReadingProgress.completed == True
        ).first()


        items.append({
            "id": s.id,
            "name": s.name,
            "library_id": s.library_id,
            "start_year": first_issue.year,
            "thumbnail_path": f"/api/comics/{first_issue.id}/thumbnail" if first_issue else None,
            "created_at": s.created_at,
            "read": bool(has_read_any)
        })

    return {
        "total": total,
        "page": params.page,
        "size": params.size,
        "items": items
    }


@router.post("/{series_id}/star", name="star")
async def star_series(series_id: int, db: SessionDep, current_user: CurrentUser):
    # Check if series exists
    series = db.query(Series).get(series_id)
    if not series: raise HTTPException(404)

    # Get or create preference
    pref = db.query(UserSeries).filter_by(user_id=current_user.id, series_id=series_id).first()

    if not pref:
        pref = UserSeries(user_id=current_user.id, series_id=series_id)
        db.add(pref)

    pref.is_starred = True
    pref.starred_at = datetime.now(timezone.utc)
    db.commit()
    return {"starred": True}


@router.delete("/{series_id}/star", name="unstar")
async def unstar_series(series_id: int, db: SessionDep, current_user: CurrentUser):
    pref = db.query(UserSeries).filter_by(user_id=current_user.id, series_id=series_id).first()
    if pref:
        pref.is_starred = False
        pref.starred_at = None
        db.commit()
    return {"starred": False}


@router.post("/{series_id}/thumbnails", name="regenerate_thumbnails")
async def regenerate_thumbnails(
        series_id: int,
        background_tasks: BackgroundTasks,
        db: SessionDep,
        admin: AdminUser  # Admin only
):
    """
    Force regenerate thumbnails for all issues in this series.
    Runs in background.
    """
    series = db.query(Series).get(series_id)
    if not series: raise HTTPException(404)

    def _task():
        # Create a new session for the background thread (Standard pattern)
        # Or relying on the service to handle it if designed that way.
        # Since our service takes a session in init, we need to be careful
        # about Session threading.
        # Better pattern for simple tasks:
        from app.database import SessionLocal
        with SessionLocal() as session:
            service = ThumbnailService(session)
            service.process_series_thumbnails(series_id)
            print(f"Finished regenerating thumbnails for series {series_id}")

    background_tasks.add_task(_task)

    return {"message": "Thumbnail regeneration started"}


@router.get("/{series_id}/recommendations", name="recommendations")
async def get_series_recommendations(
        series_id: int,
        db: SessionDep,
        user: CurrentUser,
        limit: int = 10
):
    """
    Smart Recommendations Engine.
    Returns a list of 'Lanes' based on metadata connections.
    """
    # 1. Fetch Source Series & Permissions
    source = db.query(Series).filter(Series.id == series_id).first()
    if not source:
        return []  # Or 404

    # RLS: Define visible series IDs
    # We will filter ALL recommendation queries by this list to ensure security
    visible_series_query = db.query(Series.id)
    if not user.is_superuser:
        allowed_ids = [l.id for l in user.accessible_libraries]
        visible_series_query = visible_series_query.filter(Series.library_id.in_(allowed_ids))

    # We execute this subquery in the filters below using .in_(...)
    # OR we can join, but .in_ is often cleaner for "Security Filter" logic.

    lanes = []

    # Helper to get "Sample Comic" for metadata (Publisher, Writer, Group)
    # We grab the first issue of the first volume
    sample_comic = db.query(Comic).join(Volume).filter(Volume.series_id == series_id).first()

    if not sample_comic:
        return []

    # --- STRATEGY 1: SERIES GROUP (Tightest Connection) ---
    # e.g., "Hellboy", "B.P.R.D."
    if sample_comic.series_group:
        group_matches = (
            db.query(Series)
            .join(Volume).join(Comic)
            .filter(Comic.series_group == sample_comic.series_group)
            .filter(Series.id != series_id)  # Exclude self
            .filter(Series.id.in_(visible_series_query))
            .distinct()
            .limit(limit)
            .all()
        )
        if len(group_matches) >= 1:
            lanes.append({
                "title": f"More in '{sample_comic.series_group}'",
                "items": [series_to_simple_dict(s, db, user) for s in group_matches]
            })

    # --- STRATEGY 2: TOP WRITERS (Personal Connection) (Iterative & Strict) ---
    # Find the most frequent writer in this series
    top_writers = (
        db.query(Person.name)
        .join(ComicCredit).join(Comic).join(Volume)
        .filter(Volume.series_id == series_id)
        .filter(ComicCredit.role == 'writer')
        .group_by(Person.name)
        .order_by(func.count(Person.id).desc())
        .limit(3)
        .all()
    )

    for row in top_writers:
        writer_name = row[0]

        writer_matches = (
            db.query(Series)
            .join(Volume).join(Comic).join(ComicCredit).join(Person)
            .filter(Person.name == writer_name)
            .filter(ComicCredit.role == 'writer')  # STRICT ROLE CHECK
            .filter(Series.id != series_id)
            .filter(Series.id.in_(visible_series_query))
            .distinct()
            .limit(limit)
            .all()
        )

        if len(writer_matches) >= 3:
            lanes.append({
                "title": f"More by {writer_name}",
                "items": [series_to_simple_dict(s, db, user) for s in writer_matches]
            })
            break

    # --- STRATEGY 2b: TOP PENCILLERS (Visual Connection) (Iterative & Strict) ---
    top_pencillers = (
        db.query(Person.name)
        .join(ComicCredit).join(Comic).join(Volume)
        .filter(Volume.series_id == series_id)
        .filter(ComicCredit.role == 'penciller')
        .group_by(Person.name)
        .order_by(func.count(Person.id).desc())
        .limit(3)
        .all()
    )

    for row in top_pencillers:
        penciller_name = row[0]

        # Avoid showing "More by Frank Miller (Art)" if we already have "More by Frank Miller"
        if any(penciller_name in l['title'] for l in lanes):
            continue

        penciller_matches = (
            db.query(Series)
            .join(Volume).join(Comic).join(ComicCredit).join(Person)
            .filter(Person.name == penciller_name)
            .filter(ComicCredit.role == 'penciller')  # <--- STRICT ROLE CHECK
            .filter(Series.id != series_id)
            .filter(Series.id.in_(visible_series_query))
            .distinct()
            .limit(limit)
            .all()
        )

        if len(penciller_matches) >= 3:
            lanes.append({
                "title": f"More by {penciller_name} (Art)",
                "items": [series_to_simple_dict(s, db, user) for s in penciller_matches]
            })
            break


    # --- STRATEGY 3: GENRE (Thematic Connection) ---
    # Find primary genre
    top_genre = (
        db.query(Genre.name)
        .join(comic_genres).join(Comic).join(Volume)
        .filter(Volume.series_id == series_id)
        .group_by(Genre.name)
        .order_by(func.count(Comic.id).desc())
        .first()
    )

    if top_genre:
        genre_name = top_genre[0]
        genre_matches = (
            db.query(Series)
            .join(Volume).join(Comic).join(comic_genres).join(Genre)
            .filter(Genre.name == genre_name)
            .filter(Series.id != series_id)
            .filter(Series.id.in_(visible_series_query))
            .distinct()
            .limit(limit)
            .all()
        )
        if len(genre_matches) >= 5:  # Higher threshold for genres as they are broad
            lanes.append({
                "title": f"More {genre_name} Comics",
                "items": [series_to_simple_dict(s, db, user) for s in genre_matches]
            })

    # --- STRATEGY 4: PUBLISHER (Corporate Connection) ---
    if sample_comic.publisher:
        pub_matches = (
            db.query(Series)
            .join(Volume).join(Comic)
            .filter(Comic.publisher == sample_comic.publisher)
            .filter(Series.id != series_id)
            .filter(Series.id.in_(visible_series_query))
            .distinct()
            .limit(limit)
            .all()
        )
        # Only show if we haven't already filled the UI with specific stuff
        # or if we have a lot of matches
        if len(pub_matches) >= 5 and len(lanes) < 3:
            lanes.append({
                "title": f"More from {sample_comic.publisher}",
                "items": [series_to_simple_dict(s, db, user) for s in pub_matches]
            })

    # --- STRATEGY 5: RECENT IN LIBRARY (Fallback) ---
    # If we have very few recommendations, show "New in this Library"
    if len(lanes) < 2:
        lib_matches = (
            db.query(Series)
            .filter(Series.library_id == source.library_id)
            .filter(Series.id != series_id)
            .filter(Series.id.in_(visible_series_query))
            .order_by(Series.created_at.desc())
            .limit(limit)
            .all()
        )
        if lib_matches:
            lanes.append({
                "title": f"New in {source.library.name}",
                "items": [series_to_simple_dict(s, db, user) for s in lib_matches]
            })

    return lanes


