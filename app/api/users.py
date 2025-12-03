import os
import shutil
from fastapi import APIRouter, status, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from typing import List, Annotated, Optional
from pydantic import BaseModel
from datetime import datetime
from pathlib import Path
from sqlalchemy import func


from app.api.deps import SessionDep, AdminUser, CurrentUser
from app.models.comic import Comic
from app.models.user import User
from app.models.library import Library
from app.models.reading_progress import ReadingProgress
from app.models.pull_list import PullList
from app.core.security import get_password_hash
from app.config import settings
from app.services.images import ImageService

router = APIRouter()

MAX_AVATAR_SIZE_BYTES = 5 * 1024 * 1024 # 5 MB

# Schemas
class UserCreateRequest(BaseModel):
    username: str
    email: str
    password: str
    is_superuser: bool = False
    library_ids: List[int] = []

class UserUpdateRequest(BaseModel):
    password: Optional[str] = None
    is_superuser: Optional[bool] = None
    is_active: Optional[bool] = None
    library_ids: Optional[List[int]] = None


class UserListResponse(BaseModel):
    id: int
    username: str
    email: str
    is_superuser: bool
    is_active: bool
    last_login: Optional[datetime]
    created_at: datetime
    # We don't necessarily need to return the full library objects in the list view,
    # but we might want the IDs for the edit form.
    accessible_library_ids: List[int] = []


@router.get("/me/dashboard")
async def get_user_dashboard(db: SessionDep, current_user: CurrentUser):
    """
    Aggregate stats and lists for the User Dashboard.
    """
    # 1. Calculate Stats
    # Join Progress -> Comic to get page counts
    stats_query = db.query(
        func.count(ReadingProgress.id).label('issues_read'),
        func.sum(Comic.page_count).label('total_pages')
    ).join(Comic, ReadingProgress.comic_id == Comic.id) \
        .filter(
        ReadingProgress.user_id == current_user.id,
        ReadingProgress.completed == True
    ).first()

    issues_read = stats_query.issues_read or 0
    total_pages = stats_query.total_pages or 0

    # Calculate Time (1.25 mins per page)
    total_minutes = int(total_pages * 1.25)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    time_read_str = f"{hours}h {minutes}m"

    # 2. Get Pull Lists (Limit 5 for dashboard overview)
    pull_lists = db.query(PullList).filter(PullList.user_id == current_user.id) \
        .order_by(PullList.updated_at.desc()).limit(5).all()

    # 3. Get "Continue Reading" (Limit 6)
    # We fetch the progress rows, then format them
    recent_progress = db.query(ReadingProgress).filter(
        ReadingProgress.user_id == current_user.id,
        ReadingProgress.completed == False,
        ReadingProgress.current_page > 0
    ).order_by(ReadingProgress.last_read_at.desc()).limit(6).all()

    continue_reading = []
    for p in recent_progress:
        continue_reading.append({
            "comic_id": p.comic.id,
            "series_name": p.comic.volume.series.name,
            "number": p.comic.number,
            "percentage": p.progress_percentage,
            "thumbnail": f"/api/comics/{p.comic.id}/thumbnail"
        })

    return {
        "user": {
            "username": current_user.username,
            "created_at": current_user.created_at,
            "avatar_url": f"/api/users/{current_user.id}/avatar" if current_user.avatar_path else None
        },
        "stats": {
            "issues_read": issues_read,
            "pages_turned": total_pages,
            "time_read": time_read_str
        },
        "pull_lists": [{"id": pl.id, "name": pl.name, "count": len(pl.items)} for pl in pull_lists],
        "continue_reading": continue_reading
    }


@router.post("/me/avatar")
async def upload_avatar(
        file: UploadFile = File(...),
        db: SessionDep = SessionDep,
        current_user: CurrentUser = CurrentUser
):
    """Upload and save user avatar"""
    # HTTP Validation
    if file.content_type not in ["image/jpeg", "image/png", "image/webp"]:
        raise HTTPException(400, "Invalid image format")

    content = await file.read()
    if len(content) > MAX_AVATAR_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File too large. Maximum size is 5MB."
        )

    # Create directory
    #upload_dir = Path("./storage/avatars")
    upload_dir = settings.avatar_dir
    upload_dir.mkdir(parents=True, exist_ok=True)

    filename = f"user_{current_user.id}.webp"
    file_path = upload_dir / filename

    svc = ImageService()
    success = svc.process_avatar(content, file_path)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to process image")

    # Update Database
    current_user.avatar_path = str(file_path)
    db.commit()

    return {
        "message": "Avatar updated",
        "url": f"/api/users/{current_user.id}/avatar"
    }

# Helper to serve avatar (add to users router or generic image router)
@router.get("/{user_id}/avatar")
async def get_avatar(user_id: int, db: SessionDep):
    """Serve user avatar"""
    user = db.query(User).filter(User.id == user_id).first()

    # Check if user exists and has an avatar set
    if not user or not user.avatar_path:
        raise HTTPException(status_code=404, detail="Avatar not found")

    file_path = Path(user.avatar_path)

    # Check if file physically exists
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Avatar file missing")

    return FileResponse(file_path)

# 1. List Users
@router.get("/", response_model=List[UserListResponse])
async def list_users(
        db: SessionDep,
        admin: AdminUser
):
    users = db.query(User).all()
    # Helper to format response with IDs
    results = []
    for u in users:
        results.append({
            **u.__dict__,
            "accessible_library_ids": [lib.id for lib in u.accessible_libraries]
        })

    return users


# 2. Create User (Admin Only)
@router.post("/", response_model=UserListResponse)
async def create_user(
        user_in: UserCreateRequest,
        db: SessionDep,
        admin: AdminUser
):
    existing = db.query(User).filter(User.username == user_in.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    # Fetch Libraries
    libraries = []
    if user_in.library_ids:
        libraries = db.query(Library).filter(Library.id.in_(user_in.library_ids)).all()

    user = User(
        username=user_in.username,
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        is_superuser=user_in.is_superuser,
        is_active=True,
        accessible_libraries = libraries
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        **user.__dict__,
        "accessible_library_ids": [lib.id for lib in user.accessible_libraries]
    }


# 3. Update User (e.g. Change Password)
@router.patch("/{user_id}")
async def update_user(
        user_id: int,
        updates: UserUpdateRequest,
        db: SessionDep,
        admin: AdminUser
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if updates.password:
        user.hashed_password = get_password_hash(updates.password)
    if updates.is_superuser is not None:
        user.is_superuser = updates.is_superuser
    if updates.is_active is not None:
        user.is_active = updates.is_active

    # Update Libraries
    if updates.library_ids is not None:
        libraries = db.query(Library).filter(Library.id.in_(updates.library_ids)).all()
        user.accessible_libraries = libraries

    db.commit()
    return {"message": "User updated"}


# 4. Delete User
@router.delete("/{user_id}")
async def delete_user(
        user_id: int,
        db: SessionDep,
        admin: AdminUser
):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    db.delete(user)
    db.commit()
    return {"message": "User deleted"}