from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy import func, case, Float, and_, literal
from sqlalchemy.orm import joinedload, aliased
from typing import List, Optional, Annotated
from datetime import datetime, timezone
from collections import defaultdict

from app.core.comic_helpers import (get_format_filters, get_smart_cover,
                                    get_reading_time, NON_PLAIN_FORMATS, REVERSE_NUMBERING_SERIES,
                                    get_series_age_restriction, get_comic_age_restriction)
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
        "thumbnail_path": f"/api/comics/{comic.id}/thumbnail"
    }


def bulk_serialize_series(series_list: List[Series], db, current_user) -> List[dict]:
    if not series_list: return []
    series_ids = [s.id for s in series_list]

    # --- MATH-BASED SORTING ---
    # OPTIMIZATION: Identify Reverse Series IDs in Python
    # This avoids joining the Series table in the heavy window function query.
    reverse_names = [n.lower() for n in REVERSE_NUMBERING_SERIES]

    # Check which of the current batch are reverse series
    reverse_series_ids = [
        s.id for s in series_list
        if s.name.lower() in reverse_names
    ]

    # Logic: If series_id is in our list, sort by (Number * -1). Else (Number * 1).
    sort_expression = case(
        (Volume.series_id.in_(reverse_series_ids), -1),
        else_=1
    )

    subquery = (
        db.query(
            Comic.id, Comic.year, Volume.series_id,
            func.row_number().over(
                partition_by=Volume.series_id,
                order_by=(func.cast(Comic.number, Float) * sort_expression)
            ).label("rn")
        )
        .join(Volume)
        .filter(Volume.series_id.in_(series_ids))
        .subquery()
    )

    covers = db.query(subquery).filter(subquery.c.rn == 1).all()
    cover_map = {row.series_id: row for row in covers}

    # 2. Batch Fetch Read Status (If user logged in)
    read_status_map = {}
    if current_user:
        # Calculate Total Comics vs Read Comics per Series
        stats = (
            db.query(Volume.series_id, func.count(Comic.id).label('total'),
                     func.count(ReadingProgress.id).label('read_count'))
            .select_from(Comic).join(Volume)
            .outerjoin(ReadingProgress,
                       and_(ReadingProgress.comic_id == Comic.id, ReadingProgress.user_id == current_user.id,
                            ReadingProgress.completed == True))
            .filter(Volume.series_id.in_(series_ids)).group_by(Volume.series_id).all()
        )
        for row in stats:
            read_status_map[row.series_id] = (row.total > 0) and (row.read_count >= row.total)

    # 3. Stitch it all together
    results = []
    for s in series_list:
        cover = cover_map.get(s.id)
        results.append({
            "id": s.id, "name": s.name,
            "start_year": cover.year if cover else None,
            "thumbnail_path": f"/api/comics/{cover.id}/thumbnail" if cover else None,
            "read": read_status_map.get(s.id, False)
        })

    return results


@router.get("/{series_id}", name="detail")
async def get_series_detail(series: SeriesDep, db: SessionDep, current_user: CurrentUser):
    """
    Get series summary.
    OPTIMIZED:
    1. Uses UNION ALL to fetch all metadata (Writers, Artists, etc.) in 1 query instead of 5.
    2. Batch fetches volume stats.
    """

    # 0. Security Check: Age Rating "Poison Pill"
    # Since we are fetching a specific ID, we should check if this Series is allowed.
    # Note: Optimization - We could skip this query if user has no restrictions.
    if current_user.max_age_rating:
        age_filter = get_series_age_restriction(current_user)
        # Check if this specific series passes the filter
        # We query for this ID + the Filter. If None, 403.
        is_allowed = db.query(Series.id).filter(Series.id == series.id, age_filter).first()
        if not is_allowed:
            raise HTTPException(status_code=403, detail="Content restricted by age rating")


    # 1. Get Volumes (sorted by volume_number)
    volumes = db.query(Volume).filter(Volume.series_id == series.id).order_by(Volume.volume_number).all()
    volume_ids = [v.id for v in volumes]

    if not volume_ids:
        # (Return empty structure - kept same as original)
        return {
            "id": series.id, "name": series.name, "library_id": series.library_id,
            "volume_count": 0, "total_issues": 0, "volumes": [], "collections": [], "reading_lists": [], "details": {}
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
    is_standalone = (stats.plain_count == 0 and (stats.annual_count > 0 or stats.special_count > 0))

    # 3. Story Arcs
    arc_issues = db.query(Comic.id, Comic.story_arc, Comic.number) \
        .join(Volume) \
        .filter(Comic.volume_id.in_(volume_ids)) \
        .filter(Comic.story_arc != None, Comic.story_arc != "") \
        .order_by(Volume.volume_number, func.cast(Comic.number, Float), Comic.number) \
        .all()

    # Process in Python
    # Since we sorted via SQL, the first time we encounter an Arc, it is the first issue.
    story_arcs_map = {}

    for row in arc_issues:
        if row.story_arc not in story_arcs_map:
            story_arcs_map[row.story_arc] = {"name": row.story_arc, "first_issue_id": row.id, "count": 0}
        story_arcs_map[row.story_arc]["count"] += 1
    story_arcs_data = sorted(story_arcs_map.values(), key=lambda x: x['name'])

    # 4. Related Content (Lightweight)
    related_collections = db.query(Collection).join(CollectionItem).join(Comic).filter(
        Comic.volume_id.in_(volume_ids)).distinct().all()
    related_reading_lists = db.query(ReadingList).join(ReadingListItem).join(Comic).filter(
        Comic.volume_id.in_(volume_ids)).distinct().all()

    # 5. Metadata Details (OPTIMIZED: UNION ALL)
    # Instead of 5 separate heavy joins, we do one pass.

    q_writers = db.query(Person.name.label("name"), literal("writer").label("type")) \
        .join(ComicCredit).join(Comic).filter(Comic.volume_id.in_(volume_ids)).filter(ComicCredit.role == 'writer')

    q_pencillers = db.query(Person.name.label("name"), literal("penciller").label("type")) \
        .join(ComicCredit).join(Comic).filter(Comic.volume_id.in_(volume_ids)).filter(ComicCredit.role == 'penciller')

    q_chars = db.query(Character.name.label("name"), literal("character").label("type")) \
        .join(Comic.characters).filter(Comic.volume_id.in_(volume_ids))

    q_teams = db.query(Team.name.label("name"), literal("team").label("type")) \
        .join(Comic.teams).filter(Comic.volume_id.in_(volume_ids))

    q_locs = db.query(Location.name.label("name"), literal("location").label("type")) \
        .join(Comic.locations).filter(Comic.volume_id.in_(volume_ids))

    meta_rows = q_writers.union_all(q_pencillers, q_chars, q_teams, q_locs).distinct().all()

    details = {"writers": [], "pencillers": [], "characters": [], "teams": [], "locations": []}
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

    for k in details: details[k].sort()

    # 6. Series Cover & Resume
    base_query = db.query(Comic).filter(Comic.volume_id.in_(volume_ids))
    first_issue = get_smart_cover(base_query, series_name=series.name)
    colors = first_issue.color_palette or {} if first_issue else {}

    resume_comic_id = None
    read_status = "new"

    # Check for last read comic in this series
    last_read = db.query(ReadingProgress).join(Comic).join(Volume) \
        .filter(Volume.series_id == series.id, ReadingProgress.user_id == current_user.id) \
        .order_by(ReadingProgress.last_read_at.desc()).first()

    if last_read:
        resume_comic_id = last_read.comic_id
        read_status = "in_progress"
    elif first_issue:
        resume_comic_id = first_issue.id

    # 7. Volumes Data (Batch Fetch)
    vol_stats = (
        db.query(Comic.volume_id, func.count(Comic.id).label('total'),
                 func.count(ReadingProgress.id).label('read_count'))
        .outerjoin(ReadingProgress,
                   and_(ReadingProgress.comic_id == Comic.id, ReadingProgress.user_id == current_user.id,
                        ReadingProgress.completed == True))
        .filter(Comic.volume_id.in_(volume_ids)).group_by(Comic.volume_id).all()
    )
    vol_stats_map = {row.volume_id: row for row in vol_stats}

    # B. Volume Covers (First Issue per Volume)
    # Fetch ALL comics meta for smart selection (Lightweight query)
    all_comics_meta = (db.query(Comic.id, Comic.volume_id, Comic.number, Comic.format)
                       .filter(Comic.volume_id.in_(volume_ids)).all())

    # Group by Volume
    volume_comics_map = defaultdict(list)
    for c in all_comics_meta:
        volume_comics_map[c.volume_id].append(c)

    # Helper: Check Format
    def is_standard_format(fmt: str) -> bool:
        if not fmt: return True
        f = fmt.lower()
        return f not in NON_PLAIN_FORMATS

    def issue_sort_key(c):
        try:
            return float(c.number)
        except:
            return 999999

    # Check for Gimmick Series Name once
    is_reverse_series = series.name.lower() in REVERSE_NUMBERING_SERIES

    volumes_data = []
    for vol in volumes:
        stat = vol_stats_map.get(vol.id)
        count = stat.total if stat else 0
        read_count = stat.read_count if stat else 0

        # SMART COVER LOGIC
        v_comics = volume_comics_map.get(vol.id, [])
        cover_id = None

        if v_comics:
            # 1. Prefer Standards
            standards = [c for c in v_comics if is_standard_format(c.format)]
            pool = standards if standards else v_comics

            # 2. Try Issue #1
            # We ONLY look for #1 if this is a standard series.
            # If it's a Reverse Series (Countdown), #1 is the END, not the cover.
            issue_ones = []
            if not is_reverse_series:
                issue_ones = [c for c in pool if c.number == '1']

            if issue_ones:
                cover_id = issue_ones[0].id
            else:
                # 3. Sort by Lowest Number
                pool.sort(key=issue_sort_key)

                # 4. Gimmick Selector
                if is_reverse_series:
                    # Take the HIGHEST number (Last item)
                    # e.g. Countdown #51
                    cover_id = pool[-1].id
                else:
                    # Take the LOWEST number (First item)
                    # e.g. Amazing Spider-Man #10
                    cover_id = pool[0].id

        volumes_data.append({
            "volume_id": vol.id, "volume_number": vol.volume_number,
            "first_issue_id": cover_id, # Replaces the SQL window function result
            "issue_count": count, "read": (count > 0 and read_count >= count)
        })

    # Starred Check
    is_starred = False
    if current_user:
        pref = db.query(UserSeries).filter(UserSeries.user_id == current_user.id,
                                           UserSeries.series_id == series.id).first()
        is_starred = pref.is_starred if pref else False

    return {
        "id": series.id, "name": series.name, "library_id": series.library_id, "library_name": series.library.name,
        "publisher": stats.publisher, "imprint": stats.imprint, "start_year": stats.start_year,
        "volume_count": len(volumes), "total_issues": stats.plain_count,
        "annual_count": stats.annual_count, "special_count": stats.special_count, "is_standalone": is_standalone,
        "total_pages": total_pages, "file_size": stats.total_size or 0, "read_time": read_time,
        "starred": is_starred, "first_issue_id": first_issue.id if first_issue else None,
        "volumes": volumes_data,
        "collections": [{"id": c.id, "name": c.name, "description": c.description} for c in related_collections],
        "reading_lists": [{"id": l.id, "name": l.name, "description": l.description} for l in related_reading_lists],
        "story_arcs": story_arcs_data, "details": details,
        "resume_to": {"comic_id": resume_comic_id, "status": read_status},
        "colors": colors, "is_admin": current_user.is_superuser,
        "is_reverse_numbering": is_reverse_series,
    }


# ... (Keep the rest of the file: get_series_issues, list_series, etc.) ...
@router.get("/{series_id}/issues", response_model=PaginatedResponse, name="issues")
async def get_series_issues(
        current_user: CurrentUser,
        series_id: int,
        params: Annotated[PaginationParams, Depends()],
        db: SessionDep,
        type: Annotated[str, Query(pattern="^(plain|annual|special|all)$")] = "plain",
        read_filter: Annotated[str, Query(pattern="^(all|read|unread)$")] = "all",
        sort_order: Annotated[str, Query(pattern="^(asc|desc)$")] = "asc"
):
    """
    Get paginated issues for a series, filtered by type, read status with sort option
    Defaults to ASC, unless series is a known 'Reverse Numbering' title.
    """

    # Fetch Series Name for Gimmick Detection
    # We need the name to check the list.
    # Optimization: We can just fetch the name column.
    series_name = db.query(Series.name).filter(Series.id == series_id).scalar()

    # Determine Sort Order
    if sort_order is None:
        if series_name and series_name.lower() in REVERSE_NUMBERING_SERIES:
            sort_order = "desc"
        else:
            sort_order = "asc"

    # Select Comic AND the completed status
    query = db.query(Comic, ReadingProgress.completed).outerjoin(
        ReadingProgress,
        (ReadingProgress.comic_id == Comic.id) & (ReadingProgress.user_id == current_user.id)
    ).join(Volume).join(Series).filter(Series.id == series_id)

    # --- AGE RATING FILTER ---
    # Even if the Series is allowed, we double-check individual issues (defensive coding)
    # or just rely on the Series check?
    # Logic: If the series is allowed, technically all comics are allowed (Poison Pill).
    # BUT: If we change logic later to "Partial View", this line saves us.
    age_filter = get_comic_age_restriction(current_user)
    if age_filter is not None:
        query = query.filter(age_filter)
    # -------------------------


    # Format filters
    is_plain, is_annual, is_special = get_format_filters()

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
        query = query.filter((ReadingProgress.completed == None) | (ReadingProgress.completed == False))

    # Smart Sorting Strategy
    # We define the 3-stage sort keys:
    # 1. Volume (Major)
    # 2. Numeric Value (9 before 10)
    # 3. String Value (10a before 10b)
    sort_keys = [Volume.volume_number, func.cast(Comic.number, Float), Comic.number]
    if sort_order == "desc":
        # Reverse ALL keys to ensure "Vol 2 #10" comes before "Vol 1 #1"
        query = query.order_by(*[k.desc() for k in sort_keys])
    else:
        # Default Ascending
        query = query.order_by(*[k.asc() for k in sort_keys])

    # Pagination & Execute
    total = query.count()

    comics = query.offset(params.skip).limit(params.size).all()

    items = []
    for comic, is_completed in comics:
        data = comic_to_simple_dict(comic)
        data['read'] = True if is_completed else False
        items.append(data)

    return {"total": total, "page": params.page, "size": params.size, "items": items}


@router.get("/", response_model=PaginatedResponse, name="list")
async def list_series(
        db: SessionDep, current_user: CurrentUser, params: Annotated[PaginationParams, Depends()],
        only_starred: bool = False, sort_by: Annotated[str, Query(pattern="^(name|created|updated)$")] = "name",
        sort_desc: bool = False
):
    query = db.query(Series)

    # 1. Apply Security Filter (unless Superuser)
    if not current_user.is_superuser:
        allowed_ids = [lib.id for lib in current_user.accessible_libraries]
        query = query.filter(Series.library_id.in_(allowed_ids))

        # --- AGE RATING FILTER (Only for non-superusers? Or everyone?) --- # TODO Only normal users for now
        # Usually admins want to see everything, but if an admin sets a restriction on themselves for testing...
        # Let's apply it based on the user object, regardless of superuser status,
        # UNLESS your requirement is that Admins bypass age checks.
        # Standard: Admins bypass permissions, but usually respect explicit filters.
        # Let's respect the fields on the user object.
        age_filter = get_series_age_restriction(current_user)
        if age_filter is not None:
            query = query.filter(age_filter)
        # -------------------------

    # Filter Starred
    if only_starred:
        query = query.join(UserSeries).filter(UserSeries.user_id == current_user.id, UserSeries.is_starred == True)

    # 2. Apply Sorting
    if sort_by == "created":
        sort_col = Series.created_at
    elif sort_by == "updated":
        sort_col = Series.updated_at
    else:
        sort_col = case((Series.name.ilike("The %"), func.substr(Series.name, 5)), else_=Series.name)

    if sort_desc:
        query = query.order_by(sort_col.desc())
    else:
        query = query.order_by(sort_col.asc())

    # 3. Pagination
    total = query.count()
    series_list = query.offset(params.skip).limit(params.size).all()

    # USE HELPER instead of loop
    items = bulk_serialize_series(series_list, db, current_user)

    final_items = []
    for s, item in zip(series_list, items):
        # item is the dict from bulk_serialize
        item['created_at'] = s.created_at
        item['library_id'] = s.library_id
        final_items.append(item)

    return {"total": total, "page": params.page, "size": params.size, "items": final_items}


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
async def regenerate_thumbnails(series_id: int, background_tasks: BackgroundTasks, db: SessionDep, admin: AdminUser):
    series = db.query(Series).get(series_id)
    if not series: raise HTTPException(404)

    def _task():
        # Create a new session for the background thread (Standard pattern)
        # Or relying on the service to handle it if designed that way.
        # Since our service takes a session in init, we need to be careful
        # about Session threading.
        # Better pattern for simple tasks:
        from app.database import SessionLocal
        import logging
        logger = logging.getLogger(__name__)

        try:
            with SessionLocal() as session:
                service = ThumbnailService(session)
                service.process_series_thumbnails(series_id)
        except Exception as e:
            logger.exception(f"Background thumbnail generation failed for series {series_id}: {e}")

    background_tasks.add_task(_task)

    return {"message": "Thumbnail regeneration started"}


@router.get("/{series_id}/recommendations", name="recommendations")
async def get_series_recommendations(series_id: int, db: SessionDep, user: CurrentUser, limit: int = 10):
    source = db.query(Series).filter(Series.id == series_id).first()
    if not source: return []

    # RLS: Define visible series IDs
    # We will filter ALL recommendation queries by this list to ensure security
    visible_series_query = db.query(Series.id)
    if not user.is_superuser:
        allowed_ids = [l.id for l in user.accessible_libraries]
        visible_series_query = visible_series_query.filter(Series.library_id.in_(allowed_ids))

        # --- AGE RATING FILTER ---
        age_filter = get_series_age_restriction(user)
        if age_filter is not None:
            visible_series_query = visible_series_query.filter(age_filter)
        # -------------------------

    # We execute this subquery in the filters below using .in_(...)
    # OR we can join, but .in_ is often cleaner for "Security Filter" logic.

    lanes = []

    # Helper to get "Sample Comic" for metadata (Publisher, Writer, Group)
    # We grab the first issue of the first volume
    sample_comic = db.query(Comic).join(Volume).filter(Volume.series_id == series_id).first()
    if not sample_comic: return []

    # --- STRATEGY 1: SERIES GROUP ---
    if sample_comic.series_group:
        group_matches = db.query(Series).join(Volume).join(Comic).filter(
            Comic.series_group == sample_comic.series_group, Series.id != series_id,
            Series.id.in_(visible_series_query)).distinct().limit(limit).all()
        if len(group_matches) >= 1: lanes.append({"title": f"More in '{sample_comic.series_group}'",
                                                  "items": bulk_serialize_series(group_matches, db, user)})

    top_writers = db.query(Person.name).join(ComicCredit).join(Comic).join(Volume).filter(Volume.series_id == series_id,
                    ComicCredit.role == 'writer').group_by(Person.name).order_by(func.count(Person.id).desc()).limit(3).all()

    for row in top_writers:
        writer_name = row[0]
        writer_matches = db.query(Series).join(Volume).join(Comic).join(ComicCredit).join(Person).filter(
            Person.name == writer_name, ComicCredit.role == 'writer', Series.id != series_id,
            Series.id.in_(visible_series_query)).distinct().limit(limit).all()
        if len(writer_matches) >= 3:
            lanes.append({"title": f"More by {writer_name}", "items": bulk_serialize_series(writer_matches, db, user)})
            break

    top_pencillers = db.query(Person.name).join(ComicCredit).join(Comic).join(Volume).filter(
        Volume.series_id == series_id, ComicCredit.role == 'penciller').group_by(Person.name).order_by(
        func.count(Person.id).desc()).limit(3).all()
    for row in top_pencillers:
        penciller_name = row[0]
        if any(penciller_name in l['title'] for l in lanes): continue
        penciller_matches = db.query(Series).join(Volume).join(Comic).join(ComicCredit).join(Person).filter(
            Person.name == penciller_name, ComicCredit.role == 'penciller', Series.id != series_id,
            Series.id.in_(visible_series_query)).distinct().limit(limit).all()
        if len(penciller_matches) >= 3:
            lanes.append({"title": f"More by {penciller_name} (Art)",
                          "items": bulk_serialize_series(penciller_matches, db, user)})
            break

    top_genre = db.query(Genre.name).join(comic_genres).join(Comic).join(Volume).filter(
        Volume.series_id == series_id).group_by(Genre.name).order_by(func.count(Comic.id).desc()).first()
    if top_genre:
        genre_name = top_genre[0]
        genre_matches = db.query(Series).join(Volume).join(Comic).join(comic_genres).join(Genre).filter(
            Genre.name == genre_name, Series.id != series_id, Series.id.in_(visible_series_query)).distinct().limit(
            limit).all()
        if len(genre_matches) >= 5: lanes.append(
            {"title": f"More {genre_name} Comics", "items": bulk_serialize_series(genre_matches, db, user)})

    if sample_comic.publisher:
        pub_matches = db.query(Series).join(Volume).join(Comic).filter(Comic.publisher == sample_comic.publisher,
                                                                       Series.id != series_id, Series.id.in_(
                visible_series_query)).distinct().limit(limit).all()
        if len(pub_matches) >= 5 and len(lanes) < 3: lanes.append(
            {"title": f"More from {sample_comic.publisher}", "items": bulk_serialize_series(pub_matches, db, user)})

    if len(lanes) < 2:
        lib_matches = db.query(Series).filter(Series.library_id == source.library_id, Series.id != series_id,
                                              Series.id.in_(visible_series_query)).order_by(
            Series.created_at.desc()).limit(limit).all()
        if lib_matches: lanes.append(
            {"title": f"New in {source.library.name}", "items": bulk_serialize_series(lib_matches, db, user)})

    return lanes


