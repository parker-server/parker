from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Annotated
from pydantic import BaseModel
from datetime import datetime
import json

from app.api.deps import SessionDep, CurrentUser
from app.models.saved_search import SavedSearch
from app.schemas.search import SearchRequest

router = APIRouter()


# Schemas
class SavedSearchCreate(BaseModel):
    name: str
    query: SearchRequest  # Re-use your existing schema to validate the JSON


class SavedSearchResponse(BaseModel):
    id: int
    name: str
    query: SearchRequest
    created_at: datetime


@router.get("/", response_model=List[SavedSearchResponse], name="list")
async def list_saved_searches(db: SessionDep, current_user: CurrentUser):
    """Get all saved searches for the current user"""
    searches = db.query(SavedSearch).filter(SavedSearch.user_id == current_user.id).order_by(
        SavedSearch.created_at.desc()).all()

    return [
        {
            "id": s.id,
            "name": s.name,
            "query": json.loads(s.query_json),
            "created_at": s.created_at
        }
        for s in searches
    ]


@router.post("/", response_model=SavedSearchResponse, name="create")
async def save_search(
        data: SavedSearchCreate,
        db: SessionDep,
        current_user: CurrentUser
):
    """Save a search configuration"""
    # Verify name uniqueness for user? Optional.

    saved = SavedSearch(
        user_id=current_user.id,
        name=data.name,
        query_json=data.query.json()  # Serialize Pydantic model to JSON string
    )
    db.add(saved)
    db.commit()
    db.refresh(saved)

    return {
        "id": saved.id,
        "name": saved.name,
        "query": json.loads(saved.query_json),
        "created_at": saved.created_at
    }


@router.delete("/{search_id}", name="delete")
async def delete_saved_search(
        search_id: int,
        db: SessionDep,
        current_user: CurrentUser
):
    saved = db.query(SavedSearch).filter(
        SavedSearch.id == search_id,
        SavedSearch.user_id == current_user.id
    ).first()

    if not saved:
        raise HTTPException(404, "Saved search not found")

    db.delete(saved)
    db.commit()
    return {"message": "Deleted"}