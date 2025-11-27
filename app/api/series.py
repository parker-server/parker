from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, case, or_
from typing import List, Optional, Annotated

from app.core.comic_helpers import NON_PLAIN_FORMATS, get_format_filters, get_smart_cover

from app.api.deps import SessionDep, CurrentUser
from app.api.deps import PaginationParams, PaginatedResponse

# Import related models
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.collection import Collection, CollectionItem
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.credits import Person, ComicCredit
from app.models.tags import Character, Team, Location



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
        func.max(Comic.publisher).label('publisher')
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

    return {
        "id": series.id,
        "name": series.name,
        "publisher": stats.publisher,
        "start_year": stats.start_year,
        "volume_count": len(volumes),
        "total_issues": stats.plain_count,
        "annual_count": stats.annual_count,
        "special_count": stats.special_count,
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
        }
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

    # Sort: Volume Number -> Issue Number
    comics = query.order_by(Volume.volume_number, Comic.number) \
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

    # 2. Apply Sorting
    if sort_by == "created":
        sort_col = Series.created_at
    elif sort_by == "updated":
        sort_col = Series.updated_at
    else:
        sort_col = Series.name

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
            "thumbnail_path": f"/api/comics/{first_issue.id}/thumbnail" if first_issue else None,
            "created_at": s.created_at
        })

    return {
        "total": total,
        "page": params.page,
        "size": params.size,
        "items": items
    }
