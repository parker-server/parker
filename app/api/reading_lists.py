from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import joinedload

from app.api.deps import SessionDep, CurrentUser
from app.core.comic_helpers import get_aggregated_metadata
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.tags import Character, Team, Location
from app.models.credits import Person, ComicCredit
from app.models.reading_list import ReadingList, ReadingListItem

router = APIRouter()


@router.get("/", name="list")
async def list_reading_lists(db: SessionDep, current_user: CurrentUser):
    """List reading lists, hiding ones that are empty due to permissions."""

    # Determine Permissions
    allowed_ids = set()
    is_superuser = current_user.is_superuser
    if not is_superuser:
        allowed_ids = {lib.id for lib in current_user.accessible_libraries}

    # Fetch Lists with eager loading
    reading_lists = db.query(ReadingList).options(
        joinedload(ReadingList.items).joinedload(ReadingListItem.comic).joinedload(Comic.volume).joinedload(
            Volume.series)
    ).all()


    result = []
    for rl in reading_lists:

        # Calculate "Visible Count"
        visible_count = 0
        if is_superuser:
            visible_count = len(rl.items)
        else:
            visible_count = sum(
                1 for item in rl.items
                if item.comic and item.comic.volume.series.library_id in allowed_ids
            )

        # Hide if empty
        if visible_count == 0:
            continue

        result.append({
            "id": rl.id,
            "name": rl.name,
            "description": rl.description,
            "auto_generated": bool(rl.auto_generated),
            "comic_count": len(rl.items),
            "created_at": rl.created_at,
            "updated_at": rl.updated_at
        })

    return {
        "total": len(result),
        "reading_lists": result
    }


@router.get("/{list_id}", name="detail")
async def get_reading_list(list_id: int, db: SessionDep, current_user: CurrentUser):
    """Get a specific reading list with all comics in order"""
    reading_list = db.query(ReadingList).filter(ReadingList.id == list_id).first()

    if not reading_list:
        raise HTTPException(status_code=404, detail="Reading list not found")

    # Security Scope
    allowed_ids = None
    if not current_user.is_superuser:
        allowed_ids = [lib.id for lib in current_user.accessible_libraries]

    # 1. Get comics (Ordered by Position) (Scoped)
    # We join Comic to ensure we can access fields efficiently
    # We must join Series to filter by library
    query = db.query(ReadingListItem).join(Comic).join(Volume).join(Series) \
        .options(joinedload(ReadingListItem.comic).joinedload(Comic.volume).joinedload(Volume.series)) \
        .filter(ReadingListItem.reading_list_id == list_id)

    if allowed_ids is not None:
        query = query.filter(Series.library_id.in_(allowed_ids))

    items = query.order_by(ReadingListItem.position).all()

    comics = []
    for item in items:
        if not item.comic: continue
        comic = item.comic
        comics.append({
            "position": item.position,
            "id": comic.id,
            "series_id": comic.volume.series_id,
            "series": comic.volume.series.name,
            "volume": comic.volume.volume_number,
            "number": comic.number,
            "title": comic.title,
            "filename": comic.filename,
            "year": comic.year,
            "format": comic.format,
            "thumbnail_path": f"/api/comics/{comic.id}/thumbnail"
        })

    if len(comics) <= 0:
        raise HTTPException(status_code=404, detail="No comics found")

    # 2. Aggregated Metadata (scoped)
    details = {
        "writers": get_aggregated_metadata(db, Person, ReadingListItem, ReadingListItem.reading_list_id, list_id,'writer', allowed_library_ids=allowed_ids),
        "pencillers": get_aggregated_metadata(db, Person, ReadingListItem, ReadingListItem.reading_list_id, list_id,'penciller', allowed_library_ids=allowed_ids),
        "characters": get_aggregated_metadata(db, Character, ReadingListItem, ReadingListItem.reading_list_id, list_id, allowed_library_ids=allowed_ids),
        "teams": get_aggregated_metadata(db, Team, ReadingListItem, ReadingListItem.reading_list_id, list_id, allowed_library_ids=allowed_ids),
        "locations": get_aggregated_metadata(db, Location, ReadingListItem, ReadingListItem.reading_list_id, list_id, allowed_library_ids=allowed_ids)
    }

    return {
        "id": reading_list.id,
        "name": reading_list.name,
        "description": reading_list.description,
        "auto_generated": bool(reading_list.auto_generated),
        "comic_count": len(comics),
        "comics": comics,
        "created_at": reading_list.created_at,
        "updated_at": reading_list.updated_at,
        "details": details
    }

@router.delete("/{list_id}", name="delete")
async def delete_reading_list(list_id: int, db: SessionDep, current_user: CurrentUser):
    """Delete a reading list"""
    reading_list = db.query(ReadingList).filter(ReadingList.id == list_id).first()

    if not reading_list:
        raise HTTPException(status_code=404, detail="Reading list not found")

    db.delete(reading_list)
    db.commit()

    return {"message": f"Reading list '{reading_list.name}' deleted"}