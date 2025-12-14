from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import joinedload, aliased
from sqlalchemy import func, select, and_, or_

from app.api.deps import SessionDep, CurrentUser
from app.core.comic_helpers import get_aggregated_metadata, get_age_rating_config, get_banned_comic_condition, check_container_restriction
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.tags import Character, Team, Location
from app.models.credits import Person, ComicCredit
from app.models.reading_list import ReadingList, ReadingListItem

router = APIRouter()


@router.get("/", name="list")
async def list_reading_lists(db: SessionDep, current_user: CurrentUser):
    """
    List reading lists.
    OPTIMIZED: Uses a SQL subquery to count visible items instead of fetching all rows.
    """

    # 1. Prepare Security Filter
    is_superuser = current_user.is_superuser
    allowed_ids = []
    if not is_superuser:
        allowed_ids = [lib.id for lib in current_user.accessible_libraries]

    # 2. Build Correlated Subquery for Count
    # "Select count(items) where item.list_id = outer.id AND item is accessible"

    # We alias ReadingListItem to avoid confusion with the outer query
    item_alias = aliased(ReadingListItem)

    count_stmt = select(func.count(item_alias.id)) \
        .join(Comic, item_alias.comic_id == Comic.id) \
        .join(Volume, Comic.volume_id == Volume.id) \
        .join(Series, Volume.series_id == Series.id) \
        .where(item_alias.reading_list_id == ReadingList.id)

    # Apply RLS to the count
    if not is_superuser:
        count_stmt = count_stmt.where(Series.library_id.in_(allowed_ids))

    # scalar_subquery() lets us use this as a column in the main query
    visible_count_col = count_stmt.scalar_subquery()

    # 3. Main Query: Fetch List + Calculated Count
    # Filter where visible_count > 0 (Hide empty lists)
    query = db.query(ReadingList, visible_count_col.label("v_count")) \
        .filter(visible_count_col > 0) \
        .order_by(ReadingList.name) \
        .all()

    # --- AGE RATING POISON PILL ---
    if current_user.max_age_rating:
        banned_condition = get_banned_comic_condition(current_user)
        # Filter out Reading Lists that contain ANY banned comic
        query = query.filter(
            ~ReadingList.items.any(ReadingListItem.comic.has(banned_condition))
        )
    # ------------------------------

    results = query.order_by(ReadingList.name).all()

    # 4. Format Results
    response = []
    for rl, v_count in results:
        response.append({
            "id": rl.id,
            "name": rl.name,
            "description": rl.description,
            "auto_generated": bool(rl.auto_generated),
            "comic_count": v_count,  # Use the SQL calculated count
            "created_at": rl.created_at,
            "updated_at": rl.updated_at
        })

    return {
        "total": len(response),
        "reading_lists": response
    }


@router.get("/{list_id}", name="detail")
async def get_reading_list(list_id: int, db: SessionDep, current_user: CurrentUser):
    """Get a specific reading list with all comics in order"""

    # --- 1. SECURITY: POISON PILL CHECK (FAIL FASt) ---
    check_container_restriction(
        db, current_user,
        ReadingListItem,
        ReadingListItem.reading_list_id,
        list_id,
        "Reading list"
    )
    # --------------------------------------

    reading_list = db.query(ReadingList).filter(ReadingList.id == list_id).first()

    if not reading_list:
        raise HTTPException(status_code=404, detail="Reading list not found")

    # Security Scope
    allowed_ids = None
    if not current_user.is_superuser:
        allowed_ids = [lib.id for lib in current_user.accessible_libraries]

    # 1. Get comics (Ordered by Position) (Scoped)
    # Eager load relationships to prevent N+1
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

    # (Empty lists are valid in some UIs, but keeping 404 behavior)
    if len(comics) <= 0:
        raise HTTPException(status_code=404, detail="No comics found (or access denied)")

    # 2. Aggregated Metadata (scoped)
    details = {
        "writers": get_aggregated_metadata(db, Person, ReadingListItem, ReadingListItem.reading_list_id, list_id,
                                           'writer', allowed_library_ids=allowed_ids),
        "pencillers": get_aggregated_metadata(db, Person, ReadingListItem, ReadingListItem.reading_list_id, list_id,
                                              'penciller', allowed_library_ids=allowed_ids),
        "characters": get_aggregated_metadata(db, Character, ReadingListItem, ReadingListItem.reading_list_id, list_id,
                                              allowed_library_ids=allowed_ids),
        "teams": get_aggregated_metadata(db, Team, ReadingListItem, ReadingListItem.reading_list_id, list_id,
                                         allowed_library_ids=allowed_ids),
        "locations": get_aggregated_metadata(db, Location, ReadingListItem, ReadingListItem.reading_list_id, list_id,
                                             allowed_library_ids=allowed_ids)
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
    if not reading_list: raise HTTPException(status_code=404, detail="Reading list not found")
    db.delete(reading_list)
    db.commit()

    return {"message": f"Reading list '{reading_list.name}' deleted"}