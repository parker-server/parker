from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, SessionDep
from app.core.comic_helpers import ensure_comic_access
from app.models.bookmark import Bookmark
from app.services.bookmarks import BookmarkService

router = APIRouter()


class SaveBookmarkRequest(BaseModel):
    page_index: int = Field(ge=0)
    label: Optional[str] = Field(default=None, max_length=120)


class UpdateBookmarkRequest(BaseModel):
    label: Optional[str] = Field(default=None, max_length=120)


def get_bookmark_service(
    db: SessionDep,
    user: CurrentUser,
) -> BookmarkService:
    return BookmarkService(db, user_id=user.id)


def serialize_bookmark(bookmark: Bookmark) -> dict:
    return {
        "id": bookmark.id,
        "comic_id": bookmark.comic_id,
        "page_index": bookmark.page_index,
        "label": bookmark.label,
        "created_at": bookmark.created_at,
        "updated_at": bookmark.updated_at,
    }


@router.get("/comic/{comic_id}", name="comic_bookmarks")
async def get_comic_bookmarks(
    comic_id: int,
    service: Annotated[BookmarkService, Depends(get_bookmark_service)],
    db: SessionDep,
    current_user: CurrentUser,
):
    ensure_comic_access(db, current_user, comic_id)
    return [serialize_bookmark(bookmark) for bookmark in service.list_for_comic(comic_id)]


@router.post("/comic/{comic_id}", name="save_comic_bookmark")
async def save_comic_bookmark(
    comic_id: int,
    request: SaveBookmarkRequest,
    service: Annotated[BookmarkService, Depends(get_bookmark_service)],
    db: SessionDep,
    current_user: CurrentUser,
):
    comic = ensure_comic_access(db, current_user, comic_id)

    if comic.page_count is not None and comic.page_count > 0 and request.page_index >= comic.page_count:
        raise HTTPException(status_code=422, detail="Bookmark page is out of range")

    try:
        bookmark, created = service.save_bookmark(
            comic_id=comic_id,
            page_index=request.page_index,
            label=request.label,
        )
        db.commit()
        db.refresh(bookmark)
        return {
            "created": created,
            "bookmark": serialize_bookmark(bookmark),
        }
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))


@router.patch("/{bookmark_id}", name="update_bookmark")
async def update_bookmark(
    bookmark_id: int,
    request: UpdateBookmarkRequest,
    service: Annotated[BookmarkService, Depends(get_bookmark_service)],
    db: SessionDep,
    current_user: CurrentUser,
):
    bookmark = service.get_bookmark(bookmark_id)
    if not bookmark:
        raise HTTPException(status_code=404, detail="Bookmark not found")

    ensure_comic_access(db, current_user, bookmark.comic_id)

    try:
        updated = service.rename_bookmark(bookmark_id, request.label)
        db.commit()
        db.refresh(updated)
        return serialize_bookmark(updated)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{bookmark_id}", name="delete_bookmark")
async def delete_bookmark(
    bookmark_id: int,
    service: Annotated[BookmarkService, Depends(get_bookmark_service)],
    db: SessionDep,
    current_user: CurrentUser,
):
    bookmark = service.get_bookmark(bookmark_id)
    if not bookmark:
        raise HTTPException(status_code=404, detail="Bookmark not found")

    ensure_comic_access(db, current_user, bookmark.comic_id)

    try:
        deleted = service.delete_bookmark(bookmark_id)
        db.commit()
        return {
            "bookmark_id": deleted.id,
            "message": "Bookmark deleted",
        }
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
