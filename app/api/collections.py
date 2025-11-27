from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Annotated

from app.api.deps import get_db, get_current_user
from app.models.collection import Collection, CollectionItem
from app.models.comic import Comic
from app.models.user import User

router = APIRouter()


@router.get("/")
async def list_collections(current_user: Annotated[User, Depends(get_current_user)],
                           db: Session = Depends(get_db)):
    """List all collections"""
    collections = db.query(Collection).all()

    result = []
    for col in collections:
        result.append({
            "id": col.id,
            "name": col.name,
            "description": col.description,
            "auto_generated": bool(col.auto_generated),
            "comic_count": len(col.items),
            "created_at": col.created_at,
            "updated_at": col.updated_at
        })

    return {
        "total": len(result),
        "collections": result
    }


@router.get("/{collection_id}")
async def get_collection(current_user: Annotated[User, Depends(get_current_user)],
                         collection_id: int, db: Session = Depends(get_db)):
    """Get a specific collection with all comics"""
    collection = db.query(Collection).filter(Collection.id == collection_id).first()

    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    # Get all comics (no specific order)
    comics = []
    for item in collection.items:
        comic = item.comic
        comics.append({
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
        "id": collection.id,
        "name": collection.name,
        "description": collection.description,
        "auto_generated": bool(collection.auto_generated),
        "comic_count": len(comics),
        "comics": comics,
        "created_at": collection.created_at,
        "updated_at": collection.updated_at
    }


@router.delete("/{collection_id}")
async def delete_collection(current_user: Annotated[User, Depends(get_current_user)],
                            collection_id: int, db: Session = Depends(get_db)):
    """Delete a collection"""
    collection = db.query(Collection).filter(Collection.id == collection_id).first()

    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    db.delete(collection)
    db.commit()

    return {"message": f"Collection '{collection.name}' deleted"}