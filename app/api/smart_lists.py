from fastapi import APIRouter, Depends, HTTPException
from typing import List
import json

from app.api.deps import SessionDep, CurrentUser
from app.models.smart_list import SmartList
from app.services.search import SearchService
from app.schemas.search import SearchRequest
from app.schemas.smart_list import SmartListResponse, SmartListCreate, SmartListUpdate

router = APIRouter()


@router.post("/")
def create_smart_list(
        data: SmartListCreate,
        db: SessionDep,
        current_user: CurrentUser
):
    """Save the current search configuration as a Smart List"""
    smart_list = SmartList(
        user_id=current_user.id,
        name=data.name,
        query_config=data.query.model_dump()  # Save the JSON
    )
    db.add(smart_list)
    db.commit()
    return smart_list


@router.get("/{list_id}/items")
def execute_smart_list(
        list_id: int,
        db: SessionDep,
        current_user: CurrentUser,
        limit: int = 20
):
    """The 'Auto-Fire' Endpoint: Loads config -> Runs Search -> Returns Results"""

    # 1. Fetch Config
    smart_list = db.query(SmartList).filter(SmartList.id == list_id).first()
    if not smart_list:
        raise HTTPException(status_code=404, detail="List not found")

    # 2. Rehydrate Request
    # We override the limit/offset to ensure it fits the 'Rail' UI
    config = smart_list.query_config.copy()
    config['limit'] = limit
    config['offset'] = 0

    # 3. Execute via SearchService
    # This reuses ALL your existing logic (filtering, sorting, tags, etc.)
    search_req = SearchRequest(**config)
    service = SearchService(db, current_user)
    results = service.search(search_req)

    return {
        "id": smart_list.id,
        "name": smart_list.name,
        "icon": smart_list.icon,
        "items": results['results']  # The comic_card compatible list
    }


@router.get("/", response_model=List[SmartListResponse])
def get_my_smart_lists(db: SessionDep, current_user: CurrentUser):
    """List all smart lists for the current user."""
    smart_lists = db.query(SmartList).filter(SmartList.user_id == current_user.id).all()

    return [
        {
            "id": s.id,
            "name": s.name,
            "icon": s.icon,
            "show_on_dashboard": s.show_on_dashboard,
            "query": s.query_config,
            "created_at": s.created_at
        }
        for s in smart_lists
    ]



@router.delete("/{list_id}")
def delete_smart_list(list_id: int, db: SessionDep, current_user: CurrentUser):
    """Delete a smart list."""
    slist = db.query(SmartList).filter(SmartList.id == list_id, SmartList.user_id == current_user.id).first()
    if not slist:
        raise HTTPException(status_code=404, detail="List not found")

    db.delete(slist)
    db.commit()
    return {"message": "Deleted"}


@router.patch("/{list_id}")
def update_smart_list(
        list_id: int,
        updates: SmartListUpdate,
        db: SessionDep,
        current_user: CurrentUser
):
    """Update name, icon, visibility or query settings."""
    slist = db.query(SmartList).filter(SmartList.id == list_id, SmartList.user_id == current_user.id).first()
    if not slist:
        raise HTTPException(status_code=404, detail="List not found")

    if updates.name is not None:
        slist.name = updates.name
    if updates.icon is not None:
        slist.icon = updates.icon
    if updates.show_on_dashboard is not None:
        slist.show_on_dashboard = updates.show_on_dashboard
    if updates.show_in_library is not None:
        slist.show_in_library = updates.show_in_library
    if updates.query is not None:
        slist.query = updates.query = updates.query

    db.commit()
    db.refresh(slist)
    return slist