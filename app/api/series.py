from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, case, or_
from typing import List, Optional, Annotated

from app.api.deps import SessionDep, CurrentUser

from app.models.comic import Comic, Volume
from app.models.series import Series

# Import related models
from app.models.collection import Collection, CollectionItem
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.credits import Person, ComicCredit
from app.models.tags import Character, Team, Location

from app.api.deps import PaginationParams, PaginatedResponse

router = APIRouter()

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

    # Define "Plain Issue" logic (Not an Annual/Special)
    is_plain = or_(
        Comic.format == None,
        func.lower(Comic.format).not_in(NON_PLAIN_FORMATS)
    )

    # 2. Aggregation Stats (Counts)
    # ... (Same as before) ...
    is_annual = func.lower(Comic.format) == 'annual'
    is_special = (func.lower(Comic.format) != 'annual') & (func.lower(Comic.format).in_(NON_PLAIN_FORMATS))

    stats = db.query(
        func.count(case((is_plain, 1))).label('plain_count'),
        func.count(case((is_annual, 1))).label('annual_count'),
        func.count(case((is_special, 1))).label('special_count'),
        func.min(Comic.year).label('start_year'),
        func.max(Comic.publisher).label('publisher')
    ).filter(Comic.volume_id.in_(volume_ids)).first()

    # 3. Related Content & Metadata (Collections, Reading Lists, Credits)
    # ... (Same distinct queries as before) ...
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
    first_issue = db.query(Comic) \
        .filter(Comic.volume_id.in_(volume_ids)) \
        .filter(is_plain) \
        .filter(Comic.number != '0') \
        .order_by(Comic.year, Comic.number) \
        .first()

    # Fallback for Series Cover (if only #0 exists)
    if not first_issue:
        first_issue = db.query(Comic).filter(Comic.volume_id.in_(volume_ids)).order_by(Comic.year).first()

    # 5. VOLUMES DATA Loop (Fixed)
    volumes_data = []
    for vol in volumes:
        count = db.query(Comic).filter(Comic.volume_id == vol.id).count()

        # --- FIXED COVER LOGIC ---
        # Try to find a plain issue that is NOT #0
        vol_first = db.query(Comic) \
            .filter(Comic.volume_id == vol.id) \
            .filter(is_plain) \
            .filter(Comic.number != '0') \
            .order_by(Comic.year, Comic.number) \
            .first()

        # Fallback: If volume is ONLY issue #0 or ONLY Annuals, just take the first thing we have
        if not vol_first:
            vol_first = db.query(Comic).filter(Comic.volume_id == vol.id).order_by(Comic.year, Comic.number).first()
        # -------------------------

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

    if type == "plain":
        query = query.filter(or_(
            Comic.format == None,
            func.lower(Comic.format).not_in(NON_PLAIN_FORMATS)
        ))
    elif type == "annual":
        query = query.filter(func.lower(Comic.format) == 'annual')
    elif type == "special":
        query = query.filter(
            func.lower(Comic.format) != 'annual',
            func.lower(Comic.format).in_(NON_PLAIN_FORMATS)
        )

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