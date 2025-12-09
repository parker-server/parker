from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import Float, func
from typing import List, Annotated

from app.core.comic_helpers import get_aggregated_metadata
from app.api.deps import SessionDep, CurrentUser
from app.models.collection import Collection, CollectionItem
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.tags import Character, Team, Location
from app.models.credits import Person
from app.models.user import User

router = APIRouter()


@router.get("/", name="list")
async def list_collections(current_user: CurrentUser, db: SessionDep):
    """List collections, hiding ones that are empty due to permissions."""

    # Determine Permissions
    allowed_ids = set()
    is_superuser = current_user.is_superuser
    if not is_superuser:
        allowed_ids = {lib.id for lib in current_user.accessible_libraries}

    # Fetch Collections with eager loading to avoid N+1 during filtering
    # We need the path: Collection -> Items -> Comic -> Volume -> Series (to get library_id)
    collections = db.query(Collection).options(
        joinedload(Collection.items).joinedload(CollectionItem.comic).joinedload(Comic.volume).joinedload(
            Volume.series)
    ).all()

    result = []
    for col in collections:

        # Calculate "Visible Count"
        # If superuser, everything is visible.
        # If user, only items where series.library_id is in allowed_ids.

        visible_count = 0
        if is_superuser:
            visible_count = len(col.items)
        else:
            # Filter in Python (fast enough for collections list)
            visible_count = sum(
                1 for item in col.items
                if item.comic and item.comic.volume.series.library_id in allowed_ids
            )

        # Hide if empty
        if visible_count == 0:
            continue

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


@router.get("/{collection_id}", name="detail")
async def get_collection(current_user: CurrentUser,
                         collection_id: int, db: SessionDep):
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

    if len(comics) <= 0:
        raise HTTPException(status_code=404, detail="No comics found")

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


@router.delete("/{collection_id}", name="delete")
async def delete_collection(current_user: CurrentUser,
                            collection_id: int, db: SessionDep):
    """Delete a collection"""
    collection = db.query(Collection).filter(Collection.id == collection_id).first()

    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    db.delete(collection)
    db.commit()

    return {"message": f"Collection '{collection.name}' deleted"}