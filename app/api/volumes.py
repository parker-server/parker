from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from typing import List
from pathlib import Path

from app.database import get_db
from app.models.comic import Comic, Volume
from app.models.series import Series

router = APIRouter()

@router.get("/{volume_id}/comics/")
async def list_comics_for_volumes(volume_id: int, db: Session = Depends(get_db)):
    """List all comics for a volume"""
    comics = db.query(Comic).filter(Comic.volume_id == volume_id).all()

    result = []

    # This is a limited subset of the comic data returned.
    # No need for all the related information for this return
    for comic in comics:
        result.append({
            "id": comic.id,
            "filename": comic.filename,
            "file_path": comic.file_path,
            "thumbnail_path": comic.thumbnail_path,
            "page_count": comic.page_count,
            "number": comic.number,
            "title": comic.title,
        })

    return {
        "total": len(result),
        "comics": result
    }