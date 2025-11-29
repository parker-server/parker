from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy import func, case, Float
from typing import List, Optional, Annotated
from datetime import datetime

from app.core.comic_helpers import NON_PLAIN_FORMATS, get_format_filters, get_smart_cover

from app.api.deps import SessionDep, CurrentUser, AdminUser
from app.api.deps import PaginationParams, PaginatedResponse

# Import related models
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.collection import Collection, CollectionItem
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.credits import Person, ComicCredit
from app.models.tags import Character, Team, Location
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


@router.get("/{series_id}")
async def get_series_detail(series_id: int, db: SessionDep, current_user: CurrentUser):
    """
    Get series summary including Related content and Metadata Details.
    """
    series = db.query(Series).filter(Series.id == series_id).first()
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")

    # 1. Get Volumes
    volumes = db.query(Volume).filter(Volume.series_id == series_id).all()
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
        func.max(Comic.imprint).label('imprint')
    ).filter(Comic.volume_id.in_(volume_ids)).first()

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

    # Resume Logic
    resume_comic_id = None
    read_status = "new"

    # Check for last read comic in this series
    last_read = db.query(ReadingProgress).join(Comic).join(Volume) \
        .filter(Volume.series_id == series_id) \
        .filter(ReadingProgress.user_id == current_user.id) \
        .order_by(ReadingProgress.last_read_at.desc()) \
        .first()

    if last_read:
        resume_comic_id = last_read.comic_id
        read_status = "in_progress"
    elif first_issue:
        # Fallback to the first issue (calculated for the cover)
        resume_comic_id = first_issue.id

    # 5. VOLUMES DATA Loop (Fixed)
    volumes_data = []
    for vol in volumes:
        count = db.query(Comic).filter(Comic.volume_id == vol.id).count()

        # Scoped query for this volume
        vol_base_query = db.query(Comic).filter(Comic.volume_id == vol.id)
        vol_first = get_smart_cover(vol_base_query)

        volumes_data.append({
            "volume_id": vol.id,
            "volume_number": vol.volume_number,
            "first_issue_id": vol_first.id if vol_first else None,
            "issue_count": count
        })

    # Check if starred
    is_starred = False
    if current_user:
        pref = db.query(UserSeries).filter(
            UserSeries.user_id == current_user.id,
            UserSeries.series_id == series_id
        ).first()
        if pref and pref.is_starred:
            is_starred = True


    return {
        "id": series.id,
        "name": series.name,
        "library_id": series.library_id,
        "publisher": stats.publisher,
        "imprint": stats.imprint,
        "start_year": stats.start_year,
        "volume_count": len(volumes),
        "total_issues": stats.plain_count,
        "annual_count": stats.annual_count,
        "special_count": stats.special_count,
        "starred": is_starred,
        "first_issue_id": first_issue.id if first_issue else None,
        "volumes": volumes_data,
        "collections": [{"id": c.id, "name": c.name, "description": c.description} for c in related_collections],
        "reading_lists": [{"id": l.id, "name": l.name, "description": l.description} for l in related_reading_lists],
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
    }


@router.get("/{series_id}/issues", response_model=PaginatedResponse)
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
    query = db.query(Comic).join(Volume).join(Series).filter(Series.id == series_id)

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

    return {
        "total": total,
        "page": params.page,
        "size": params.size,
        "items": [comic_to_simple_dict(c) for c in comics]
    }


@router.get("/", response_model=PaginatedResponse)
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

        items.append({
            "id": s.id,
            "name": s.name,
            "library_id": s.library_id,
            "start_year": first_issue.year,
            "thumbnail_path": f"/api/comics/{first_issue.id}/thumbnail" if first_issue else None,
            "created_at": s.created_at
        })

    return {
        "total": total,
        "page": params.page,
        "size": params.size,
        "items": items
    }


@router.post("/{series_id}/star")
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
    pref.starred_at = datetime.utcnow()
    db.commit()
    return {"starred": True}


@router.delete("/{series_id}/star")
async def unstar_series(series_id: int, db: SessionDep, current_user: CurrentUser):
    pref = db.query(UserSeries).filter_by(user_id=current_user.id, series_id=series_id).first()
    if pref:
        pref.is_starred = False
        pref.starred_at = None
        db.commit()
    return {"starred": False}


@router.post("/{series_id}/thumbnails")
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

