from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, asc
from typing import List

from app.database import get_db
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.credits import Person, ComicCredit
from app.models.tags import Character, Team, Location
from app.api.deps import PaginationParams, PaginatedResponse

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
        "thumbnail_path": f"/api/comics/{comic.id}/thumbnail"
    }


@router.get("/{volume_id}")
async def get_volume_detail(volume_id: int, db: Session = Depends(get_db)):
    """
    Get volume summary and aggregated metadata.
    """
    volume = db.query(Volume).join(Series).filter(Volume.id == volume_id).first()
    if not volume:
        raise HTTPException(status_code=404, detail="Volume not found")

    # 1. Get Basic Stats
    issue_count = db.query(Comic).filter(Comic.volume_id == volume_id).count()
    year_range = db.query(
        func.min(Comic.year),
        func.max(Comic.year)
    ).filter(Comic.volume_id == volume_id).first()

    # 2. Find Cover (First issue sorted by number)
    first_issue = db.query(Comic) \
        .filter(Comic.volume_id == volume_id) \
        .order_by(Comic.year, Comic.number) \
        .first()

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
        "issue_count": issue_count,
        "start_year": year_range[0],
        "end_year": year_range[1],
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
        volume_id: int,
        params: PaginationParams = Depends(),
        db: Session = Depends(get_db)
):
    """
    Get paginated issues for a specific volume.
    """
    query = db.query(Comic).filter(Comic.volume_id == volume_id)

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