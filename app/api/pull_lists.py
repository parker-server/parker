from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, not_

from app.core.comic_helpers import (get_aggregated_metadata, get_series_age_restriction,
                                    get_thumbnail_url, get_banned_comic_condition)
from app.api.deps import SessionDep, CurrentUser
from app.models.pull_list import PullList, PullListItem
from app.models.comic import Comic
from app.models.series import Series
from app.models.comic import Volume
from app.models.tags import Character, Team, Location
from app.models.credits import Person, ComicCredit


from app.schemas.pull_list import PullListCreate, PullListUpdate, AddComicRequest, ReorderRequest, BatchAddComicRequest

router = APIRouter()


@router.get("/", name="list")
def get_my_lists(db: SessionDep, current_user: CurrentUser):
    """List all pull lists for the current user."""

    # Don't hide the list just because of one bad apple.
    # We return all lists, and filter the items inside the detail view.
    query = db.query(PullList).filter(PullList.user_id == current_user.id)

    return query.order_by(PullList.name).all()

@router.post("/", name="create")
def create_list(list_data: PullListCreate, db: SessionDep, current_user: CurrentUser):
    """Create a new pull list."""
    new_list = PullList(
        user_id=current_user.id,
        name=list_data.name,
        description=list_data.description
    )
    db.add(new_list)
    db.commit()
    db.refresh(new_list)
    return new_list


@router.get("/{list_id}", name="detail")
def get_list_details(list_id: int, db: SessionDep, current_user: CurrentUser):
    """
    Get list details + items sorted by user preference.
    OPTIMIZED: Uses joinedload to prevent N+1 queries on items loop.
    SECURED: Filters out banned items instead of blocking the whole list.
    """

    # 1. Fetch List Metadata (No items joined yet)
    plist = db.query(PullList).filter(
        PullList.id == list_id,
        PullList.user_id == current_user.id
    ).first()

    if not plist:
        raise HTTPException(status_code=404, detail="Pull list not found")

    # 2. Fetch Items (Secure Item Filter)
    # We query PullListItem directly. This allows us to:
    # A. Join Comic/Series to check Age Ratings
    # B. Filter OUT the banned items via SQL
    # C. Sort via SQL (Efficient)
    query = db.query(PullListItem).join(Comic).join(Volume).join(Series) \
        .options(joinedload(PullListItem.comic).joinedload(Comic.volume).joinedload(Volume.series)) \
        .filter(PullListItem.pull_list_id == list_id)

    # Apply Series Poison Pill
    # This hides:
    # 1. Explicit Comics
    # 2. "Safe" Comics that belong to an Explicit Series
    series_filter = get_series_age_restriction(current_user)
    if series_filter is not None:
        query = query.filter(series_filter)

    items = query.order_by(PullListItem.sort_order).all()

    # 2. Build formatted item list (In-Memory, efficient now)
    items_data = []

    for item in items:
        if not item.comic: continue

        items_data.append({
            "id": item.comic.id,
            "item_id": item.id,
            "title": item.comic.title,
            # These accesses are now safe/cached
            "series_name": item.comic.volume.series.name,
            "volume_number": item.comic.volume.volume_number,
            "number": item.comic.number,
            "thumbnail_path": get_thumbnail_url(item.comic.id, item.comic.updated_at),
            "sort_order": item.sort_order,
            "read": False  # we could join ReadingProgress here in the future
        })

    # 3. Aggregated Metadata (5 queries, acceptable for detail view)
    details = {
        "writers": get_aggregated_metadata(db, Person, PullListItem, PullListItem.pull_list_id, list_id, 'writer'),
        "pencillers": get_aggregated_metadata(db, Person, PullListItem, PullListItem.pull_list_id, list_id,
                                              'penciller'),
        "characters": get_aggregated_metadata(db, Character, PullListItem, PullListItem.pull_list_id, list_id),
        "teams": get_aggregated_metadata(db, Team, PullListItem, PullListItem.pull_list_id, list_id),
        "locations": get_aggregated_metadata(db, Location, PullListItem, PullListItem.pull_list_id, list_id)
    }

    return {
        "id": plist.id,
        "name": plist.name,
        "description": plist.description,
        "created_at": plist.created_at,
        "items": items_data,
        "details": details
    }


@router.put("/{list_id}", name="update")
def update_list(list_id: int, update_data: PullListUpdate, db: SessionDep, current_user: CurrentUser):
    """Rename or update description."""
    plist = db.query(PullList).filter(PullList.id == list_id, PullList.user_id == current_user.id).first()
    if not plist:
        raise HTTPException(status_code=404, detail="Pull list not found")

    if update_data.name is not None:
        plist.name = update_data.name
    if update_data.description is not None:
        plist.description = update_data.description

    db.commit()
    db.refresh(plist)
    return plist


@router.delete("/{list_id}", name="delete")
def delete_list(list_id: int, db: SessionDep, current_user: CurrentUser):
    """Delete the entire list (does not delete comics)."""
    plist = db.query(PullList).filter(PullList.id == list_id, PullList.user_id == current_user.id).first()
    if not plist:
        raise HTTPException(status_code=404, detail="Pull list not found")

    db.delete(plist)
    db.commit()
    return {"message": "List deleted"}


# --- Item Management ---

@router.post("/{list_id}/items", name="add_item")
def add_item_to_list(list_id: int, item_data: AddComicRequest, db: SessionDep, current_user: CurrentUser):
    plist = db.query(PullList).filter(PullList.id == list_id, PullList.user_id == current_user.id).first()
    if not plist: raise HTTPException(status_code=404, detail="Pull list not found")

    comic = db.query(Comic).get(item_data.comic_id)
    if not comic: raise HTTPException(status_code=404, detail="Comic not found")

    existing = db.query(PullListItem).filter(PullListItem.pull_list_id == list_id,
                                             PullListItem.comic_id == item_data.comic_id).first()
    if existing: raise HTTPException(status_code=400, detail="Comic already in this list")

    max_order = db.query(func.max(PullListItem.sort_order)).filter(PullListItem.pull_list_id == list_id).scalar()
    new_order = (max_order if max_order is not None else -1) + 1

    new_item = PullListItem(pull_list_id=list_id, comic_id=item_data.comic_id, sort_order=new_order)
    db.add(new_item)
    db.commit()

    return {"message": "Comic added", "sort_order": new_order}


@router.delete("/{list_id}/items/{comic_id}", name="remove_item")
def remove_item_from_list(list_id: int, comic_id: int, db: SessionDep, current_user: CurrentUser):
    plist = db.query(PullList).filter(PullList.id == list_id, PullList.user_id == current_user.id).first()
    if not plist: raise HTTPException(status_code=404, detail="Pull list not found")

    item = db.query(PullListItem).filter(PullListItem.pull_list_id == list_id,
                                         PullListItem.comic_id == comic_id).first()
    if not item: raise HTTPException(status_code=404, detail="Item not found in list")

    db.delete(item)
    db.commit()
    return {"message": "Item removed"}


@router.post("/{list_id}/reorder", name="reorder_list_items")
def reorder_list_items(list_id: int, order_data: ReorderRequest, db: SessionDep, current_user: CurrentUser):
    plist = db.query(PullList).filter(PullList.id == list_id, PullList.user_id == current_user.id).first()
    if not plist: raise HTTPException(status_code=404, detail="Pull list not found")

    items = db.query(PullListItem).filter(PullListItem.pull_list_id == list_id).all()
    item_map = {item.comic_id: item for item in items}

    for index, comic_id in enumerate(order_data.comic_ids):
        if comic_id in item_map:
            item_map[comic_id].sort_order = index

    db.commit()
    return {"message": "List reordered successfully"}


@router.post("/{list_id}/items/batch", name="batch_add_items")
def batch_add_items_to_list(list_id: int, batch_data: BatchAddComicRequest, db: SessionDep, current_user: CurrentUser):
    plist = db.query(PullList).filter(PullList.id == list_id, PullList.user_id == current_user.id).first()
    if not plist: raise HTTPException(status_code=404, detail="Pull list not found")

    if not batch_data.comic_ids: return {"message": "No comics selected"}

    existing_ids = db.query(PullListItem.comic_id).filter(
        PullListItem.pull_list_id == list_id,
        PullListItem.comic_id.in_(batch_data.comic_ids)
    ).all()
    existing_set = {r[0] for r in existing_ids}

    new_ids = [cid for cid in batch_data.comic_ids if cid not in existing_set]
    if not new_ids: return {"message": "All selected comics are already in this list"}

    max_order = db.query(func.max(PullListItem.sort_order)).filter(PullListItem.pull_list_id == list_id).scalar()
    current_order = (max_order if max_order is not None else -1) + 1

    new_items = []
    for cid in new_ids:
        new_items.append(PullListItem(pull_list_id=list_id, comic_id=cid, sort_order=current_order))
        current_order += 1

    db.add_all(new_items)
    db.commit()

    return {"message": f"Added {len(new_items)} comics to list"}
