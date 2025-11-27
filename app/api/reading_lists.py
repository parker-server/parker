from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import SessionDep, CurrentUser
from app.models.reading_list import ReadingList, ReadingListItem

router = APIRouter()


@router.get("/")
async def list_reading_lists(db: SessionDep, current_user: CurrentUser):
    """List all reading lists"""
    reading_lists = db.query(ReadingList).all()

    result = []
    for rl in reading_lists:
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


@router.get("/{list_id}")
async def get_reading_list(list_id: int, db: SessionDep, current_user: CurrentUser):
    """Get a specific reading list with all comics in order"""
    reading_list = db.query(ReadingList).filter(ReadingList.id == list_id).first()

    if not reading_list:
        raise HTTPException(status_code=404, detail="Reading list not found")

    # Get comics in order
    comics = []
    for item in reading_list.items:
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

    return {
        "id": reading_list.id,
        "name": reading_list.name,
        "description": reading_list.description,
        "auto_generated": bool(reading_list.auto_generated),
        "comic_count": len(comics),
        "comics": comics,
        "created_at": reading_list.created_at,
        "updated_at": reading_list.updated_at
    }


@router.delete("/{list_id}")
async def delete_reading_list(list_id: int, db: SessionDep, current_user: CurrentUser):
    """Delete a reading list"""
    reading_list = db.query(ReadingList).filter(ReadingList.id == list_id).first()

    if not reading_list:
        raise HTTPException(status_code=404, detail="Reading list not found")

    db.delete(reading_list)
    db.commit()

    return {"message": f"Reading list '{reading_list.name}' deleted"}