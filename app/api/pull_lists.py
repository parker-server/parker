from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from pydantic import BaseModel

from app.api.deps import SessionDep, CurrentUser
from app.models.pull_list import PullList, PullListItem
from app.models.comic import Comic
from app.models.series import Series  # Useful for joins if optimizing
from app.schemas.pull_list import PullListCreate, PullListUpdate, AddComicRequest, ReorderRequest

router = APIRouter()


@router.get("/")
def get_my_lists(db: SessionDep, current_user: CurrentUser):
    """List all pull lists for the current user."""
    return db.query(PullList).filter(PullList.user_id == current_user.id).all()


@router.post("/")
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


@router.get("/{list_id}")
def get_list_details(list_id: int, db: SessionDep, current_user: CurrentUser):
    """Get list details + items sorted by user preference."""
    plist = db.query(PullList).filter(
        PullList.id == list_id,
        PullList.user_id == current_user.id
    ).first()

    if not plist:
        raise HTTPException(status_code=404, detail="Pull list not found")

    # Build formatted item list
    # The relationship 'items' is already ordered by sort_order in the model definition
    items_data = []
    for item in plist.items:
        # Check for broken references (optional safety)
        if not item.comic: continue

        items_data.append({
            "id": item.comic.id,  # The Comic ID (used for reading)
            "item_id": item.id,  # The Junction ID
            "title": item.comic.title,
            "series_name": item.comic.volume.series.name,
            "volume_number": item.comic.volume.volume_number,
            "number": item.comic.number,
            "thumbnail_path": f"/api/comics/{item.comic.id}/thumbnail",
            "sort_order": item.sort_order,
            "read": False  # You could join ReadingProgress here if desired
        })

    return {
        "id": plist.id,
        "name": plist.name,
        "description": plist.description,
        "created_at": plist.created_at,
        "items": items_data
    }


@router.put("/{list_id}")
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


@router.delete("/{list_id}")
def delete_list(list_id: int, db: SessionDep, current_user: CurrentUser):
    """Delete the entire list (does not delete comics)."""
    plist = db.query(PullList).filter(PullList.id == list_id, PullList.user_id == current_user.id).first()
    if not plist:
        raise HTTPException(status_code=404, detail="Pull list not found")

    db.delete(plist)
    db.commit()
    return {"message": "List deleted"}


# --- Item Management ---

@router.post("/{list_id}/items")
def add_item_to_list(list_id: int, item_data: AddComicRequest, db: SessionDep, current_user: CurrentUser):
    """Add a comic to the bottom of the list."""
    # 1. Verify ownership
    plist = db.query(PullList).filter(PullList.id == list_id, PullList.user_id == current_user.id).first()
    if not plist:
        raise HTTPException(status_code=404, detail="Pull list not found")

    # 2. Verify Comic Exists
    comic = db.query(Comic).get(item_data.comic_id)
    if not comic:
        raise HTTPException(status_code=404, detail="Comic not found")

    # 3. Check for duplicates
    existing = db.query(PullListItem).filter(
        PullListItem.pull_list_id == list_id,
        PullListItem.comic_id == item_data.comic_id
    ).first()

    if existing:
        raise HTTPException(status_code=400, detail="Comic already in this list")

    # 4. Calculate Sort Order (Append to end)
    max_order = db.query(func.max(PullListItem.sort_order)) \
        .filter(PullListItem.pull_list_id == list_id).scalar()

    new_order = (max_order if max_order is not None else -1) + 1

    new_item = PullListItem(
        pull_list_id=list_id,
        comic_id=item_data.comic_id,
        sort_order=new_order
    )
    db.add(new_item)
    db.commit()

    return {"message": "Comic added", "sort_order": new_order}


@router.delete("/{list_id}/items/{comic_id}")
def remove_item_from_list(list_id: int, comic_id: int, db: SessionDep, current_user: CurrentUser):
    """Remove a specific comic from the list."""
    # Verify ownership
    plist = db.query(PullList).filter(PullList.id == list_id, PullList.user_id == current_user.id).first()
    if not plist:
        raise HTTPException(status_code=404, detail="Pull list not found")

    item = db.query(PullListItem).filter(
        PullListItem.pull_list_id == list_id,
        PullListItem.comic_id == comic_id
    ).first()

    if not item:
        raise HTTPException(status_code=404, detail="Item not found in list")

    db.delete(item)
    db.commit()
    return {"message": "Item removed"}


@router.post("/{list_id}/reorder")
def reorder_list_items(list_id: int, order_data: ReorderRequest, db: SessionDep, current_user: CurrentUser):
    """
    Update the sort_order for items based on a provided ordered list of IDs.
    Used by drag-and-drop UIs.
    """
    plist = db.query(PullList).filter(PullList.id == list_id, PullList.user_id == current_user.id).first()
    if not plist:
        raise HTTPException(status_code=404, detail="Pull list not found")

    # Fetch all items efficiently
    items = db.query(PullListItem).filter(PullListItem.pull_list_id == list_id).all()
    item_map = {item.comic_id: item for item in items}

    # Apply new order
    # Note: We loop through the IDs sent by the client.
    # If the client omits IDs, their order remains unchanged (or undefined behavior),
    # so the client should send the FULL list.
    for index, comic_id in enumerate(order_data.comic_ids):
        if comic_id in item_map:
            item_map[comic_id].sort_order = index

    db.commit()
    return {"message": "List reordered successfully"}