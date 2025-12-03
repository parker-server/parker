from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import Float, func
from typing import List, Annotated

from app.core.comic_helpers import get_aggregated_metadata
from app.api.deps import get_db, get_current_user
from app.models.collection import Collection, CollectionItem
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.tags import Character, Team, Location
from app.models.credits import Person
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
    """Get a specific collection with all comics and aggregated details"""
    collection = db.query(Collection).filter(Collection.id == collection_id).first()

    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    # Determine Allowed Libraries
    allowed_ids = None
    if not current_user.is_superuser:
        allowed_ids = [lib.id for lib in current_user.accessible_libraries]

    # 1. Get Comics (Sorted Chronologically) (Scoped)
    # Sort: Year -> Series Name -> Issue Number
    query = db.query(CollectionItem).join(Comic).join(Volume).join(Series) \
        .options(joinedload(CollectionItem.comic).joinedload(Comic.volume).joinedload(Volume.series)) \
        .filter(CollectionItem.collection_id == collection_id)

    # Apply library scope Filter
    if allowed_ids is not None:
        query = query.filter(Series.library_id.in_(allowed_ids))

    items = query.order_by(
        Comic.year.asc(),
        Series.name.asc(),
        func.cast(Comic.number, Float)
    ).all()

    comics = []
    for item in items:
        if not item.comic: continue
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

    # 2. Aggregated Metadata (Scoped)
    # Pass allowed_ids to the helper
    details = {
        "writers": get_aggregated_metadata(db, Person, CollectionItem, CollectionItem.collection_id, collection_id,'writer', allowed_library_ids=allowed_ids),
        "pencillers": get_aggregated_metadata(db, Person, CollectionItem, CollectionItem.collection_id, collection_id, 'penciller', allowed_library_ids=allowed_ids),
        "characters": get_aggregated_metadata(db, Character, CollectionItem, CollectionItem.collection_id, collection_id, allowed_library_ids=allowed_ids),
        "teams": get_aggregated_metadata(db, Team, CollectionItem, CollectionItem.collection_id, collection_id, allowed_library_ids=allowed_ids),
        "locations": get_aggregated_metadata(db, Location, CollectionItem, CollectionItem.collection_id, collection_id, allowed_library_ids=allowed_ids)
    }

    return {
        "id": collection.id,
        "name": collection.name,
        "description": collection.description,
        "auto_generated": bool(collection.auto_generated),
        "comic_count": len(comics),
        "comics": comics,
        "created_at": collection.created_at,
        "updated_at": collection.updated_at,
        "details": details
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