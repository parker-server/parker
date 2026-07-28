from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import joinedload, aliased
from sqlalchemy import func, select, and_, or_, not_
from typing import Annotated, List

from app.api.deps import SessionDep, CurrentUser, AdminUser, PaginationParams, PaginatedResponse
from app.core.comic_helpers import (get_aggregated_metadata,
                                    get_thumbnail_url, get_banned_comic_condition,
                                    check_container_restriction)
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.library import Library
from app.models.tags import Character, Team, Location
from app.models.credits import Person, ComicCredit
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.cbl_source import CBLSource

router = APIRouter()

SOURCE_LABELS = {
    "manual": "Manual",
    "comicinfo": "Auto-Generated",
    "cbl": "CBL Derived",
}


def _source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


class ReadingListRenameRequest(BaseModel):
    name: str


@router.get("/", response_model=PaginatedResponse, name="list")
async def list_reading_lists(db: SessionDep,
                             current_user: CurrentUser,
                             params: Annotated[PaginationParams, Depends()]):
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
        .join(Library, Series.library_id == Library.id) \
        .where(Library.parse_reading_lists == True) \
        .where(item_alias.reading_list_id == ReadingList.id)

    # Apply RLS to the count
    if not is_superuser:
        count_stmt = count_stmt.where(Series.library_id.in_(allowed_ids))

    # scalar_subquery() lets us use this as a column in the main query
    visible_count_col = count_stmt.scalar_subquery()

    # 3. Main Query: Fetch List + Calculated Count
    # Filter where visible_count > 0 (Hide empty lists)
    query = db.query(ReadingList, visible_count_col.label("v_count")) \
        .filter(visible_count_col > 0)

    # --- AGE RATING POISON PILL ---
    banned_condition = get_banned_comic_condition(current_user)
    if banned_condition is not None:
        # Filter out Reading Lists that contain ANY banned comic
        query = query.filter(
            not_(ReadingList.items.any(ReadingListItem.comic.has(banned_condition)))
        )
    # ------------------------------

    # 4. Pagination & Execute
    total = query.count()  # Count before slicing

    results = query.order_by(ReadingList.name) \
        .offset(params.skip) \
        .limit(params.size) \
        .all()

    # 4. Format Results
    items = []
    for rl, v_count in results:
        items.append({
            "id": rl.id,
            "name": rl.name,
            "description": rl.description,
            "source": rl.source,
            "source_label": _source_label(rl.source),
            "comic_count": v_count,  # Use the SQL calculated count
            "created_at": rl.created_at,
            "updated_at": rl.updated_at
        })

    return {
        "total": total,
        "page": params.page,
        "size": params.size,
        "items": items
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
    query = db.query(ReadingListItem).join(Comic).join(Volume).join(Series).join(Library) \
        .options(joinedload(ReadingListItem.comic).joinedload(Comic.volume).joinedload(Volume.series)) \
        .filter(ReadingListItem.reading_list_id == list_id, Library.parse_reading_lists == True)

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
            "thumbnail_path": get_thumbnail_url(comic.id, comic.updated_at)
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

    payload = {
        "id": reading_list.id,
        "name": reading_list.name,
        "description": reading_list.description,
        "source": reading_list.source,
        "source_label": _source_label(reading_list.source),
        "comic_count": len(comics),
        "comics": comics,
        "created_at": reading_list.created_at,
        "updated_at": reading_list.updated_at,
        "details": details
    }

    if current_user.is_superuser and reading_list.source_cbl_id:
        cbl_source = db.get(CBLSource, reading_list.source_cbl_id)
        if cbl_source:
            payload["cbl_source"] = {
                "id": cbl_source.id,
                "origin": cbl_source.origin,
                "last_refresh_status": cbl_source.last_refresh_status,
                "last_refreshed_at": cbl_source.last_refreshed_at,
            }

    return payload


@router.patch("/{list_id}", name="rename")
async def rename_reading_list(list_id: int, payload: ReadingListRenameRequest, db: SessionDep, admin: AdminUser):
    """
    Rename a reading list. Only CBL-derived lists can be renamed here --
    ComicInfo-derived names are entirely driven by embedded metadata, so fixing
    one means fixing the comic's ComicInfo.xml and rescanning, not editing the
    derived list directly. Manual lists have no creation UI yet either, so
    there's nothing to rename there today.
    """
    reading_list = db.query(ReadingList).filter(ReadingList.id == list_id).first()
    if not reading_list:
        raise HTTPException(status_code=404, detail="Reading list not found")

    if reading_list.source != "cbl":
        raise HTTPException(status_code=400, detail="Only CBL-derived reading lists can be renamed")

    new_name = payload.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")

    existing = db.query(ReadingList).filter(
        ReadingList.name == new_name, ReadingList.id != list_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="A reading list with that name already exists")

    reading_list.name = new_name
    db.commit()

    return {
        "id": reading_list.id,
        "name": reading_list.name,
        "source": reading_list.source,
        "source_label": _source_label(reading_list.source),
    }


@router.delete("/{list_id}", name="delete")
async def delete_reading_list(list_id: int, db: SessionDep, admin: AdminUser):
    """Delete a reading list. Admin-only: there's no self-service reading-list
    creation today, and comicinfo-derived lists are system-managed (deleting one
    just gets it silently recreated the next time that comic's metadata is
    reprocessed, so this was never a safe or durable action for a regular user).

    CBL-derived lists can't be deleted here at all -- CBLSourceService.delete()
    (DELETE /api/cbl-sources/{source_id}) is the only coherent lifecycle path:
    it removes the managed file and CBLSource row along with the ReadingList,
    instead of leaving an orphaned CBLSource that just gets rebuilt right back
    on the next scan."""
    reading_list = db.query(ReadingList).filter(ReadingList.id == list_id).first()
    if not reading_list: raise HTTPException(status_code=404, detail="Reading list not found")

    if reading_list.source == "cbl":
        raise HTTPException(
            status_code=400,
            detail=(
                "This is a CBL-derived reading list. Delete the CBL source instead "
                f"(DELETE /api/cbl-sources/{reading_list.source_cbl_id}) to remove it "
                "and its managed file together."
            ),
        )

    db.delete(reading_list)
    db.commit()

    return {"message": f"Reading list '{reading_list.name}' deleted"}
