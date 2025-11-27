from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, case, or_, asc
from typing import List, Annotated

from app.api.deps import SessionDep, CurrentUser
from app.api.deps import PaginationParams, PaginatedResponse

from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.credits import Person, ComicCredit
from app.models.tags import Character, Team, Location


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


@router.get("/{volume_id}")
async def get_volume_detail(volume_id: int, db: SessionDep, current_user: CurrentUser):
    """
    Get volume summary with categorized counts.
    """
    volume = db.query(Volume).join(Series).filter(Volume.id == volume_id).first()
    if not volume:
        raise HTTPException(status_code=404, detail="Volume not found")

    # 1. Categorized Counts
    is_plain = or_(
        Comic.format == None,
        func.lower(Comic.format).not_in(NON_PLAIN_FORMATS)
    )
    is_annual = func.lower(Comic.format) == 'annual'
    is_special = (func.lower(Comic.format) != 'annual') & (func.lower(Comic.format).in_(NON_PLAIN_FORMATS))

    stats = db.query(
        func.count(case((is_plain, 1))).label('plain_count'),
        func.count(case((is_annual, 1))).label('annual_count'),
        func.count(case((is_special, 1))).label('special_count'),
        func.min(Comic.year).label('start_year'),
        func.max(Comic.year).label('end_year')
    ).filter(Comic.volume_id == volume_id).first()

    # 2. Find Cover (Plain issues priority)
    first_issue = db.query(Comic) \
        .filter(Comic.volume_id == volume_id) \
        .filter(is_plain) \
        .filter(Comic.number != '0') \
        .order_by(Comic.year, Comic.number) \
        .first()

    if not first_issue:
        first_issue = db.query(Comic).filter(Comic.volume_id == volume_id).order_by(Comic.year, Comic.number).first()


    # 3. Aggregated Metadata (Scoped ONLY to this volume)
    writers = db.query(Person.name).join(ComicCredit).join(Comic) \
        .filter(Comic.volume_id == volume_id).filter(ComicCredit.role == 'writer').distinct().all()

    pencillers = db.query(Person.name).join(ComicCredit).join(Comic) \
        .filter(Comic.volume_id == volume_id).filter(ComicCredit.role == 'penciller').distinct().all()

    characters = db.query(Character.name).join(Comic.characters) \
        .filter(Comic.volume_id == volume_id).distinct().all()

    teams = db.query(Team.name).join(Comic.teams) \
        .filter(Comic.volume_id == volume_id).distinct().all()

    locations = db.query(Location.name).join(Comic.locations) \
        .filter(Comic.volume_id == volume_id).distinct().all()

    return {
        "id": volume.id,
        "volume_number": volume.volume_number,
        "series_id": volume.series.id,
        "series_name": volume.series.name,

        # Counts
        "total_issues": stats.plain_count,  # Use plain count as main count
        "annual_count": stats.annual_count,
        "special_count": stats.special_count,

        "start_year": stats.start_year,
        "end_year": stats.end_year,
        "first_issue_id": first_issue.id if first_issue else None,
        "details": {
            "writers": sorted([r[0] for r in writers]),
            "pencillers": sorted([r[0] for r in pencillers]),
            "characters": sorted([r[0] for r in characters]),
            "teams": sorted([r[0] for r in teams]),
            "locations": sorted([r[0] for r in locations])
        }
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
    query = db.query(Comic).filter(Comic.volume_id == volume_id)

    # Apply Filters
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

    # Sort by Issue Number
    comics = query.order_by(Comic.number) \
        .offset(params.skip) \
        .limit(params.size) \
        .all()

    return {
        "total": total,
        "page": params.page,
        "size": params.size,
        "items": [comic_to_simple_dict(c) for c in comics]
    }