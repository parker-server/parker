from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload, aliased
from sqlalchemy import Float, func, select, and_, or_, not_
from typing import List, Annotated

from app.core.comic_helpers import (get_aggregated_metadata, get_series_age_restriction, get_thumbnail_url,
                                    get_banned_comic_condition, check_container_restriction)
from app.api.deps import SessionDep, CurrentUser, AdminUser, PaginationParams, PaginatedResponse
from app.models.collection import Collection, CollectionItem
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.tags import Character, Team, Location
from app.models.credits import Person

router = APIRouter()


@router.get("/", response_model=PaginatedResponse, name="list")
async def list_collections(current_user: CurrentUser,
                           db: SessionDep,
                           params: Annotated[PaginationParams, Depends()]):
    """
    List collections.
    OPTIMIZED: Uses SQL subquery to count visible items instead of fetching all rows.
    """

    # 1. Prepare Security Filter
    is_superuser = current_user.is_superuser
    allowed_ids = []
    if not is_superuser:
        allowed_ids = [lib.id for lib in current_user.accessible_libraries]

    # 2. Build Correlated Subquery for Count
    item_alias = aliased(CollectionItem)

    count_stmt = select(func.count(item_alias.id)) \
        .join(Comic, item_alias.comic_id == Comic.id) \
        .join(Volume, Comic.volume_id == Volume.id) \
        .join(Series, Volume.series_id == Series.id) \
        .where(item_alias.collection_id == Collection.id)

    if not is_superuser:
        count_stmt = count_stmt.where(Series.library_id.in_(allowed_ids))

    # --- Apply Series Poison Pill to Count ---
    # This ensures comics from "Banned Series" (even if the comics themselves are safe)
    # do NOT count towards the collection's visible total.
    series_age_filter = get_series_age_restriction(current_user)
    if series_age_filter is not None:
        count_stmt = count_stmt.where(series_age_filter)
    # ----------------------------------------------

    visible_count_col = count_stmt.scalar_subquery()

    # 3. Main Query
    query = db.query(Collection, visible_count_col.label("v_count")) \
        .filter(visible_count_col > 0)

    # --- AGE RATING POISON PILL (Container level) ---
    # This checks for Explicitly Banned Comics (e.g., the specific Mature issue).
    banned_condition = get_banned_comic_condition(current_user)
    if banned_condition is not None:
        # Filter out Collections that contain ANY banned comic
        query = query.filter(
            not_(Collection.items.any(CollectionItem.comic.has(banned_condition)))
        )
    # ------------------------------

    # 4. Pagination & Execute
    total = query.count()  # Get total before slicing

    results = query.order_by(Collection.name)\
        .offset(params.skip)\
        .limit(params.size)\
        .all()

    # 5. Format
    items = []
    for col, v_count in results:
        items.append({
            "id": col.id,
            "name": col.name,
            "description": col.description,
            "auto_generated": bool(col.auto_generated),
            "comic_count": v_count,
            "created_at": col.created_at,
            "updated_at": col.updated_at
        })

    return {
        "total": total,
        "page": params.page,
        "size": params.size,
        "items": items
    }


@router.get("/{collection_id}", name="detail")
async def get_collection(current_user: CurrentUser,
                         collection_id: int, db: SessionDep):
    """Get a specific collection with all comics"""

    # --- 1. SECURITY: POISON PILL CHECK ---
    # Fail fast if this collection contains banned content
    check_container_restriction(
        db, current_user,
        CollectionItem,
        CollectionItem.collection_id,
        collection_id,
        "Collection"
    )
    # --------------------------------------

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
            "thumbnail_path": get_thumbnail_url(comic.id, comic.updated_at)
        })

    if len(comics) <= 0:
        raise HTTPException(status_code=404, detail="No comics found")

    # 2. Aggregated Metadata (Scoped)
    # Pass allowed_ids to the helper
    details = {
        "writers": get_aggregated_metadata(db, Person, CollectionItem, CollectionItem.collection_id, collection_id,
                                           'writer', allowed_library_ids=allowed_ids),
        "pencillers": get_aggregated_metadata(db, Person, CollectionItem, CollectionItem.collection_id, collection_id,
                                              'penciller', allowed_library_ids=allowed_ids),
        "characters": get_aggregated_metadata(db, Character, CollectionItem, CollectionItem.collection_id,
                                              collection_id, allowed_library_ids=allowed_ids),
        "teams": get_aggregated_metadata(db, Team, CollectionItem, CollectionItem.collection_id, collection_id,
                                         allowed_library_ids=allowed_ids),
        "locations": get_aggregated_metadata(db, Location, CollectionItem, CollectionItem.collection_id, collection_id,
                                             allowed_library_ids=allowed_ids)
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
async def delete_collection(current_user: AdminUser, collection_id: int, db: SessionDep):
    collection = db.query(Collection).filter(Collection.id == collection_id).first()
    if not collection: raise HTTPException(status_code=404, detail="Collection not found")
    db.delete(collection)
    db.commit()

    return {"message": f"Collection '{collection.name}' deleted"}