from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, status, HTTPException, UploadFile, File, Depends
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlalchemy.orm import selectinload, contains_eager
from typing import List, Annotated, Optional
from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from pathlib import Path
from sqlalchemy import func, not_, and_


from app.api.deps import SessionDep, AdminUser, CurrentUser, PaginatedResponse, PaginationParams
from app.config import settings
from app.core.comic_helpers import get_reading_time, get_banned_comic_condition, get_series_age_restriction
from app.core.security import verify_password, get_password_hash
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.user import User
from app.models.library import Library
from app.models.credits import Person, ComicCredit
from app.models.tags import Character, comic_characters
from app.models.tags import Genre, comic_genres
from app.models.reading_progress import ReadingProgress
from app.models.activity_log import ActivityLog
from app.models.pull_list import PullList, PullListItem
from app.services.images import ImageService
from app.services.settings_service import SettingsService
from app.services.statistics import StatisticsService

def get_stats_service(db: SessionDep, user: CurrentUser) -> StatisticsService:
    return StatisticsService(db, user)

router = APIRouter()

MAX_AVATAR_SIZE_BYTES = 5 * 1024 * 1024 # 5 MB

# Schemas
class UserBase(BaseModel):
    email: str | None = None
    username: str | None = None
    password: str | None = None

    @field_validator("email", "username", "password")
    def non_empty_if_provided(cls, v):
        if v is not None and not v.strip():
            raise ValueError("Field cannot be empty")
        return v


class UserCreateRequest(UserBase):
    username: str
    email: str
    password: str
    is_superuser: bool = False
    library_ids: List[int] = Field(default_factory=list)
    max_age_rating: Optional[str] = None
    allow_unknown_age_ratings: bool = False

class UserUpdateRequest(UserBase):
    password: str | None = None
    email: str
    is_superuser: bool | None = None
    is_active: bool | None = None
    library_ids: List[int] | None = None
    max_age_rating: Optional[str] = None
    allow_unknown_age_ratings: Optional[bool] = None

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
    max_age_rating: Optional[str]
    allow_unknown_age_ratings: bool

class UserPasswordUpdateRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)

class UserPreferencesResponse(BaseModel):
    user_id: int
    share_progress_enabled: bool

class UserPreferencesUpdateRequest(BaseModel):
    share_progress_enabled: Optional[bool] = None

@router.get("/me/dashboard", name="dashboard")
# Optimized Enhanced Dashboard Endpoint - Add to users.py
# Reduces number of queries and eliminates N+1 issues

@router.get("/me/dashboard", name="dashboard")
# Optimized Enhanced Dashboard Endpoint - Add to users.py
# Reduces number of queries and eliminates N+1 issues

@router.get("/me/dashboard", name="dashboard")
async def get_user_dashboard(db: SessionDep, current_user: CurrentUser):
    """
    Optimized User Dashboard - Minimizes database queries
    """

    settings_svc = SettingsService(db)
    opds_enabled = settings_svc.get("server.opds_enabled")

    stats_service = StatisticsService(db, current_user)
    dashboard_payload = stats_service.get_dashboard_payload()

    # Security filters
    series_age_filter = get_series_age_restriction(current_user)
    banned_condition = get_banned_comic_condition(current_user)

    # === PULL LISTS (with eager loading) ===
    pull_lists_query = db.query(PullList).options(selectinload(PullList.items)) \
        .filter(PullList.user_id == current_user.id) \
        .order_by(PullList.updated_at.desc())

    if banned_condition is not None:
        pull_lists_query = pull_lists_query.filter(
            not_(PullList.items.any(PullListItem.comic.has(banned_condition)))
        )

    pull_lists = pull_lists_query.limit(5).all()

    # === CONTINUE READING (with eager loading) ===
    recent_progress_query = db.query(ReadingProgress) \
        .join(ReadingProgress.comic).join(Comic.volume).join(Volume.series) \
        .options(
        contains_eager(ReadingProgress.comic)
        .contains_eager(Comic.volume)
        .contains_eager(Volume.series)
    ).filter(
        ReadingProgress.user_id == current_user.id,
        or_(ReadingProgress.completed == False, ReadingProgress.completed == None),
        ReadingProgress.current_page > 0
    )

    if series_age_filter is not None:
        recent_progress_query = recent_progress_query.filter(series_age_filter)

    recent_progress = recent_progress_query \
        .order_by(ReadingProgress.last_read_at.desc()) \
        .limit(6).all()

    continue_reading = [
        {
            "comic_id": p.comic.id,
            "series_name": p.comic.volume.series.name,
            "number": p.comic.number,
            "percentage": p.progress_percentage,
            "thumbnail": f"/api/comics/{p.comic.id}/thumbnail"
        }
        for p in recent_progress
    ]

    return {
        "opds_enabled": opds_enabled,
        "user": {
            "username": current_user.username,
            "created_at": current_user.created_at,
            "avatar_url": f"/api/users/{current_user.id}/avatar" if current_user.avatar_path else None
        },
        "pull_lists": [{"id": pl.id, "name": pl.name, "count": len(pl.items)} for pl in pull_lists],
        "continue_reading": continue_reading,
        **dashboard_payload,
    }


@router.post("/me/avatar", name="upload_avatar")
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
@router.get("/{user_id}/avatar", name="avatar")
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

@router.get("/me/preferences", name="preferences")
async def get_preferences(db: SessionDep, current_user: CurrentUser):
    """Get user preferences"""
    user = db.query(User).filter(User.id == current_user.id).first()

    # Check if user exists and has an avatar set
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "share_progress_enabled": user.share_progress_enabled,
    }

@router.patch("/me/preferences", name="update_preferences")
async def update_preferences(payload: UserPreferencesUpdateRequest, db: SessionDep, current_user: CurrentUser):
    """Update user preferences"""

    have_settings_changed = False

    if payload.share_progress_enabled is not None:
        current_user.share_progress_enabled = payload.share_progress_enabled
        have_settings_changed = True

    if have_settings_changed:
        db.add(current_user)
        db.commit()

    return {"status": "success", "message": "Preferences updated"}

@router.put("/me/password", name="update_password")
async def update_password(
    payload: UserPasswordUpdateRequest,
    db: SessionDep,
    current_user: CurrentUser
):
    """
    Allow a logged-in user to change their own password.
    """
    # 1. Verify the old password matches
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect current password")

    # 2. Hash the new password
    new_hash = get_password_hash(payload.new_password)

    # 3. Save
    current_user.hashed_password = new_hash
    db.add(current_user)
    db.commit()

    return {"status": "success", "message": "Password updated successfully"}



@router.get("/me/year-in-review", name="year_in_review")
async def get_year_in_review(
        service: Annotated[StatisticsService, Depends(get_stats_service)],
        year: Optional[int] = None
):
    """
    Generate a comprehensive Year in Review summary
    Similar to Spotify Wrapped for comic reading
    """

    # Default to current year if not specified
    if year is None:
        year = datetime.now(timezone.utc).year

    return service.get_year_wrapped(year)

# 1. List Users
@router.get("/", response_model=PaginatedResponse, tags=["admin"], name="list")
async def list_users(
        db: SessionDep,
        admin: AdminUser,
        params: Annotated[PaginationParams, Depends()],
):

    query = db.query(User)
    total = query.count()

    # OPTIMIZATION: selectinload is usually cleaner for Many-to-Many collections than joinedload
    users = query.order_by(func.lower(User.username)) \
        .options(selectinload(User.accessible_libraries)) \
        .offset(params.skip).limit(params.size).all()

    # Helper to format response with IDs
    results = []
    for u in users:
        results.append({
            "id": u.id,
            "username": u.username,
            "is_active": u.is_active,
            "is_superuser": u.is_superuser,
            "email": u.email,
            "created_at": u.created_at,
            "last_login": u.last_login,
            "accessible_library_ids": [lib.id for lib in u.accessible_libraries],
            "max_age_rating": u.max_age_rating,
            "allow_unknown_age_ratings": u.allow_unknown_age_ratings
        })


    return {
        "total": total,
        "page": params.page,
        "size": params.size,
        "items": results
    }


# Create User (Admin Only)
@router.post("/", response_model=UserListResponse, tags=["admin"], name="create")
async def create_user(
        user_in: UserCreateRequest,
        db: SessionDep,
        admin: AdminUser
):
    existing = db.query(User).filter(func.lower(User.username) == user_in.username.lower()).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    # Fetch Libraries
    libraries = []
    if not user_in.is_superuser and user_in.library_ids:
        libraries = db.query(Library).filter(Library.id.in_(user_in.library_ids)).all()

    user = User(
        username=user_in.username,
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        is_superuser=user_in.is_superuser,
        is_active=True,
        accessible_libraries = libraries,
        max_age_rating=None if user_in.is_superuser else user_in.max_age_rating,
        allow_unknown_age_ratings=False if user_in.is_superuser else user_in.allow_unknown_age_ratings
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        **user.__dict__,
        "accessible_library_ids": [lib.id for lib in user.accessible_libraries]
    }


# Update User (e.g. Change Password)
@router.patch("/{user_id}", tags=["admin"], name="update")
async def update_user(
        user_id: int,
        updates: UserUpdateRequest,
        db: SessionDep,
        admin: AdminUser
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if updates.email:
        user.email = updates.email
    if updates.password:
        user.hashed_password = get_password_hash(updates.password)
    if updates.is_superuser is not None:
        user.is_superuser = updates.is_superuser
    if updates.is_active is not None:
        user.is_active = updates.is_active

    # Update Libraries (with superuser checks)
    if user.is_superuser:

        # Super users have no library or age restrictions
        user.accessible_libraries = []
        user.max_age_rating = None
        user.allow_unknown_age_ratings = False

    else:

        if updates.library_ids is not None:
            libraries = db.query(Library).filter(Library.id.in_(updates.library_ids)).all()
            user.accessible_libraries = libraries

        if updates.max_age_rating is not None:
            # Allow clearing the rating by sending empty string, or setting it
            user.max_age_rating = updates.max_age_rating if updates.max_age_rating else None

        if updates.allow_unknown_age_ratings is not None:
            user.allow_unknown_age_ratings = updates.allow_unknown_age_ratings

    db.commit()

    return {"message": "User updated"}

# 4. Delete User
@router.delete("/{user_id}", tags=["admin"], name="delete")
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
