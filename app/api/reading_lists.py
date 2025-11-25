from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.comic import Comic

router = APIRouter()


@router.get("/")
async def list_reading_lists(db: Session = Depends(get_db)):
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
async def get_reading_list(list_id: int, db: Session = Depends(get_db)):
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
            "comic_id": comic.id,
            "series": comic.volume.series.name,
            "volume": comic.volume.volume_number,
            "number": comic.number,
            "title": comic.title,
            "filename": comic.filename,
            "year": comic.year
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
async def delete_reading_list(list_id: int, db: Session = Depends(get_db)):
    """Delete a reading list"""
    reading_list = db.query(ReadingList).filter(ReadingList.id == list_id).first()

    if not reading_list:
        raise HTTPException(status_code=404, detail="Reading list not found")

    db.delete(reading_list)
    db.commit()

    return {"message": f"Reading list '{reading_list.name}' deleted"}