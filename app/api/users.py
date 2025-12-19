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
from app.models.pull_list import PullList, PullListItem
from app.services.images import ImageService
from app.services.settings_service import SettingsService

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

    # Security filters
    series_age_filter = get_series_age_restriction(current_user)
    banned_condition = get_banned_comic_condition(current_user)

    # === COMBINED BASIC STATS QUERY ===
    # Get all reading progress stats in one query
    from sqlalchemy import case, literal_column
    from datetime import datetime, timedelta

    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)

    stats_query = db.query(
        # Basic stats
        func.count(ReadingProgress.id).label('total_progress_records'),
        func.count(case((ReadingProgress.completed == True, 1))).label('completed_comics'),
        func.sum(case((ReadingProgress.completed == True, Comic.page_count), else_=0)).label('total_pages'),
        func.count(func.distinct(Series.id)).label('series_explored'),

        # Last 30 days
        func.count(case(
            (and_(ReadingProgress.completed == True,
                  ReadingProgress.last_read_at >= thirty_days_ago), 1)
        )).label('recent_comics'),
        func.sum(case(
            (and_(ReadingProgress.completed == True,
                  ReadingProgress.last_read_at >= thirty_days_ago),
             Comic.page_count),
            else_=0
        )).label('recent_pages'),

        # Average completion time
        func.avg(
            case((ReadingProgress.completed == True,
                  func.julianday(ReadingProgress.last_read_at) -
                  func.julianday(ReadingProgress.created_at)))
        ).label('avg_completion_days')
    ).join(Comic, ReadingProgress.comic_id == Comic.id) \
        .join(Volume, Comic.volume_id == Volume.id) \
        .join(Series, Volume.series_id == Series.id) \
        .filter(ReadingProgress.user_id == current_user.id)

    if series_age_filter is not None:
        stats_query = stats_query.filter(series_age_filter)

    stats = stats_query.first()

    # Calculate derived stats
    issues_read = stats.completed_comics or 0
    total_pages = stats.total_pages or 0
    time_read_str = get_reading_time(total_pages)
    series_explored = stats.series_explored or 0

    # Reading pace classification
    avg_days = stats.avg_completion_days or 0
    reading_pace = (
        'Binge Reader' if avg_days < 1 else
        'Active Reader' if avg_days < 7 else
        'Casual Reader' if avg_days < 30 else
        'Slow Reader'
    )

    # === TOP CREATORS (Single Query with SQL sorting) ===
    from app.models.credits import Person, ComicCredit

    # Get top 3 writers and top 3 artists - let SQL limit results
    # NOTE: We don't need the full creator list, so SQL LIMIT is more efficient
    creator_stats = db.query(
        Person.name,
        ComicCredit.role,
        func.count(func.distinct(ReadingProgress.comic_id)).label('comics_read')
    ).join(ComicCredit, Person.id == ComicCredit.person_id) \
        .join(Comic, ComicCredit.comic_id == Comic.id) \
        .join(ReadingProgress, Comic.id == ReadingProgress.comic_id) \
        .join(Volume, Comic.volume_id == Volume.id) \
        .join(Series, Volume.series_id == Series.id) \
        .filter(
        ComicCredit.role.in_(['writer', 'penciller']),
        ReadingProgress.user_id == current_user.id,
        ReadingProgress.completed == True
    )

    if series_age_filter is not None:
        creator_stats = creator_stats.filter(series_age_filter)

    # Group, sort, and limit in SQL (more efficient than fetching all creators)
    creator_stats = creator_stats.group_by(Person.id, Person.name, ComicCredit.role) \
        .order_by(ComicCredit.role, func.count(func.distinct(ReadingProgress.comic_id)).desc()) \
        .all()

    # Split into writers and artists, take top 3 of each
    top_writers = [
        {'name': c.name, 'comics_read': c.comics_read}
        for c in creator_stats if c.role == 'writer'
    ][:3]

    top_artists = [
        {'name': c.name, 'comics_read': c.comics_read}
        for c in creator_stats if c.role == 'penciller'
    ][:3]

    # === TOP PUBLISHERS (Single Query with SQL sorting) ===
    publisher_stats = db.query(
        Comic.publisher,
        func.count(func.distinct(ReadingProgress.comic_id)).label('comics_read')
    ).join(ReadingProgress, Comic.id == ReadingProgress.comic_id) \
        .join(Volume, Comic.volume_id == Volume.id) \
        .join(Series, Volume.series_id == Series.id) \
        .filter(
        ReadingProgress.user_id == current_user.id,
        ReadingProgress.completed == True,
        Comic.publisher.isnot(None)
    )

    if series_age_filter is not None:
        publisher_stats = publisher_stats.filter(series_age_filter)

    # SQL sorts and limits (more efficient - only returns 3 rows)
    top_publishers = [
        {'name': p.publisher, 'comics_read': p.comics_read}
        for p in publisher_stats.group_by(Comic.publisher)
        .order_by(func.count(func.distinct(ReadingProgress.comic_id)).desc())
        .limit(3).all()
    ]

    # === GENRE BREAKDOWN (Single Query) ===
    from app.models.tags import Genre, comic_genres

    # NOTE: We need ALL genres to calculate total for percentages
    # So fetching full list and sorting in Python is appropriate here
    genre_stats = db.query(
        Genre.name,
        func.count(func.distinct(ReadingProgress.comic_id)).label('count')
    ).join(comic_genres, Genre.id == comic_genres.c.genre_id) \
        .join(Comic, comic_genres.c.comic_id == Comic.id) \
        .join(ReadingProgress, Comic.id == ReadingProgress.comic_id) \
        .join(Volume, Comic.volume_id == Volume.id) \
        .join(Series, Volume.series_id == Series.id) \
        .filter(
        ReadingProgress.user_id == current_user.id,
        ReadingProgress.completed == True
    )

    if series_age_filter is not None:
        genre_stats = genre_stats.filter(series_age_filter)

    # Fetch all genres (needed for total count and percentage calculation)
    genres = genre_stats.group_by(Genre.id, Genre.name).all()

    # Calculate total and percentages in Python
    total_genre_reads = sum(g.count for g in genres)

    # Sort in Python and take top 5 (dataset is small ~10-30 genres)
    sorted_genres = sorted(genres, key=lambda x: x.count, reverse=True)[:5]

    genre_diversity = {
        'genres_explored': len(genres),
        'top_genres': [
            {
                'name': g.name,
                'count': g.count,
                'percentage': round((g.count / total_genre_reads * 100), 1) if total_genre_reads > 0 else 0
            }
            for g in sorted_genres
        ]
    }

    # === TOP CHARACTERS (Single Query with SQL sorting) ===
    from app.models.tags import Character, comic_characters

    character_stats = db.query(
        Character.name,
        func.count(func.distinct(ReadingProgress.comic_id)).label('appearances')
    ).join(comic_characters, Character.id == comic_characters.c.character_id) \
        .join(Comic, comic_characters.c.comic_id == Comic.id) \
        .join(ReadingProgress, Comic.id == ReadingProgress.comic_id) \
        .join(Volume, Comic.volume_id == Volume.id) \
        .join(Series, Volume.series_id == Series.id) \
        .filter(
        ReadingProgress.user_id == current_user.id,
        ReadingProgress.completed == True
    )

    if series_age_filter is not None:
        character_stats = character_stats.filter(series_age_filter)

    # SQL sorts and limits (critical - could be 100-1000+ characters)
    top_characters = [
        {'name': c.name, 'appearances': c.appearances}
        for c in character_stats.group_by(Character.id, Character.name)
        .order_by(func.count(func.distinct(ReadingProgress.comic_id)).desc())
        .limit(5).all()
    ]

    # === COLLECTION STATS (Single Query) ===
    total_comics_query = db.query(func.count(Comic.id)) \
        .join(Volume, Comic.volume_id == Volume.id) \
        .join(Series, Volume.series_id == Series.id)

    if series_age_filter is not None:
        total_comics_query = total_comics_query.filter(series_age_filter)

    total_available = total_comics_query.scalar() or 0

    collection_stats = {
        'total_available': total_available,
        'read_percentage': round((issues_read / total_available * 100), 1) if total_available > 0 else 0
    }

    # Series completed count (single query)
    # A series is "completed" if user has read all its comics
    series_completion_subquery = db.query(
        Series.id,
        func.count(Comic.id).label('total_comics'),
        func.count(case((ReadingProgress.completed == True, 1))).label('completed_comics')
    ).select_from(Series) \
        .join(Volume, Volume.series_id == Series.id) \
        .join(Comic, Comic.volume_id == Volume.id) \
        .outerjoin(
        ReadingProgress,
        and_(
            ReadingProgress.comic_id == Comic.id,
            ReadingProgress.user_id == current_user.id
        )
    )

    if series_age_filter is not None:
        series_completion_subquery = series_completion_subquery.filter(series_age_filter)

    series_completion_subquery = series_completion_subquery.group_by(Series.id).subquery()

    series_completed = db.query(func.count()).select_from(series_completion_subquery).filter(
        series_completion_subquery.c.total_comics == series_completion_subquery.c.completed_comics,
        series_completion_subquery.c.completed_comics > 0
    ).scalar() or 0

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

    # === ASSEMBLE RESPONSE ===
    return {
        "opds_enabled": opds_enabled,
        "user": {
            "username": current_user.username,
            "created_at": current_user.created_at,
            "avatar_url": f"/api/users/{current_user.id}/avatar" if current_user.avatar_path else None
        },
        "stats": {
            "issues_read": issues_read,
            "pages_turned": total_pages,
            "time_read": time_read_str,
            "completed_comics": issues_read,
            "series_explored": series_explored,
            "series_completed": series_completed
        },
        "creators": {
            "top_writers": top_writers,
            "top_artists": top_artists
        },
        "publishers": {
            "top_publishers": top_publishers
        },
        "characters": {
            "top_characters": top_characters
        },
        "genres": genre_diversity,
        "reading_behavior": {
            'last_30_days': {
                'comics_read': stats.recent_comics or 0,
                'pages_read': stats.recent_pages or 0
            },
            'avg_days_to_complete': round(avg_days, 1),
            'reading_pace': reading_pace
        },
        "collection": collection_stats,
        "pull_lists": [{"id": pl.id, "name": pl.name, "count": len(pl.items)} for pl in pull_lists],
        "continue_reading": continue_reading
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


# Year in Review Endpoint - Add to users.py
# This creates a "Spotify Wrapped" style annual summary

# Year in Review Endpoint - Add to users.py
# This creates a "Spotify Wrapped" style annual summary


@router.get("/me/year-in-review", name="year_in_review")
async def get_year_in_review(
        db: SessionDep,
        current_user: CurrentUser,
        year: Optional[int] = None
):
    """
    Generate a comprehensive Year in Review summary
    Similar to Spotify Wrapped for comic reading
    """

    # Default to current year if not specified
    if year is None:
        year = datetime.now(timezone.utc).year

    # Date range for the year
    year_start = f"{year}-01-01"
    year_end = f"{year}-12-31 23:59:59"

    series_age_filter = get_series_age_restriction(current_user)

    # === BASIC YEAR STATS ===
    year_stats = db.query(
        func.count(func.distinct(ReadingProgress.comic_id)).label('comics_completed'),
        func.sum(Comic.page_count).label('total_pages'),
        func.count(func.distinct(Series.id)).label('series_explored'),
        func.count(func.distinct(Volume.id)).label('volumes_completed')
    ).join(Comic, ReadingProgress.comic_id == Comic.id) \
        .join(Volume).join(Series) \
        .filter(
        ReadingProgress.user_id == current_user.id,
        ReadingProgress.completed == True,
        ReadingProgress.last_read_at >= year_start,
        ReadingProgress.last_read_at <= year_end
    )

    if series_age_filter is not None:
        year_stats = year_stats.filter(series_age_filter)

    stats = year_stats.first()

    # === TOP CREATOR OF THE YEAR ===
    from app.models.credits import Person, ComicCredit

    top_writer = db.query(
        Person.name,
        func.count(func.distinct(ReadingProgress.comic_id)).label('comics_read')
    ).join(ComicCredit, Person.id == ComicCredit.person_id) \
        .join(Comic, ComicCredit.comic_id == Comic.id) \
        .join(ReadingProgress, Comic.id == ReadingProgress.comic_id) \
        .join(Volume).join(Series) \
        .filter(
        ComicCredit.role == 'writer',
        ReadingProgress.user_id == current_user.id,
        ReadingProgress.completed == True,
        ReadingProgress.last_read_at >= year_start,
        ReadingProgress.last_read_at <= year_end
    )

    if series_age_filter is not None:
        top_writer = top_writer.filter(series_age_filter)

    top_writer = top_writer.group_by(Person.id, Person.name) \
        .order_by(func.count(func.distinct(ReadingProgress.comic_id)).desc()) \
        .first()

    top_artist = db.query(
        Person.name,
        func.count(func.distinct(ReadingProgress.comic_id)).label('comics_read')
    ).join(ComicCredit, Person.id == ComicCredit.person_id) \
        .join(Comic, ComicCredit.comic_id == Comic.id) \
        .join(ReadingProgress, Comic.id == ReadingProgress.comic_id) \
        .join(Volume).join(Series) \
        .filter(
        ComicCredit.role == 'penciller',
        ReadingProgress.user_id == current_user.id,
        ReadingProgress.completed == True,
        ReadingProgress.last_read_at >= year_start,
        ReadingProgress.last_read_at <= year_end
    )

    if series_age_filter is not None:
        top_artist = top_artist.filter(series_age_filter)

    top_artist = top_artist.group_by(Person.id, Person.name) \
        .order_by(func.count(func.distinct(ReadingProgress.comic_id)).desc()) \
        .first()

    # === TOP SERIES ===
    top_series = db.query(
        Series.name,
        func.count(func.distinct(ReadingProgress.comic_id)).label('issues_read')
    ).join(Volume).join(Comic).join(ReadingProgress) \
        .filter(
        ReadingProgress.user_id == current_user.id,
        ReadingProgress.completed == True,
        ReadingProgress.last_read_at >= year_start,
        ReadingProgress.last_read_at <= year_end
    )

    if series_age_filter is not None:
        top_series = top_series.filter(series_age_filter)

    top_series = top_series.group_by(Series.id, Series.name) \
        .order_by(func.count(func.distinct(ReadingProgress.comic_id)).desc()) \
        .first()

    # === TOP GENRE ===
    from app.models.tags import Genre, comic_genres

    top_genre = db.query(
        Genre.name,
        func.count(func.distinct(ReadingProgress.comic_id)).label('count')
    ).join(comic_genres, Genre.id == comic_genres.c.genre_id) \
        .join(Comic, comic_genres.c.comic_id == Comic.id) \
        .join(ReadingProgress, Comic.id == ReadingProgress.comic_id) \
        .join(Volume).join(Series) \
        .filter(
        ReadingProgress.user_id == current_user.id,
        ReadingProgress.completed == True,
        ReadingProgress.last_read_at >= year_start,
        ReadingProgress.last_read_at <= year_end
    )

    if series_age_filter is not None:
        top_genre = top_genre.filter(series_age_filter)

    top_genre = top_genre.group_by(Genre.id, Genre.name) \
        .order_by(func.count(func.distinct(ReadingProgress.comic_id)).desc()) \
        .first()

    # === TOP CHARACTER ===
    from app.models.tags import Character, comic_characters

    top_character = db.query(
        Character.name,
        func.count(func.distinct(ReadingProgress.comic_id)).label('appearances')
    ).join(comic_characters, Character.id == comic_characters.c.character_id) \
        .join(Comic, comic_characters.c.comic_id == Comic.id) \
        .join(ReadingProgress, Comic.id == ReadingProgress.comic_id) \
        .join(Volume).join(Series) \
        .filter(
        ReadingProgress.user_id == current_user.id,
        ReadingProgress.completed == True,
        ReadingProgress.last_read_at >= year_start,
        ReadingProgress.last_read_at <= year_end
    )

    if series_age_filter is not None:
        top_character = top_character.filter(series_age_filter)

    top_character = top_character.group_by(Character.id, Character.name) \
        .order_by(func.count(func.distinct(ReadingProgress.comic_id)).desc()) \
        .first()

    # === BUSIEST MONTH ===
    busiest_month = db.query(
        func.strftime('%m', ReadingProgress.last_read_at).label('month'),
        func.count(func.distinct(ReadingProgress.comic_id)).label('count')
    ).join(Comic, ReadingProgress.comic_id == Comic.id) \
        .join(Volume).join(Series) \
        .filter(
        ReadingProgress.user_id == current_user.id,
        ReadingProgress.completed == True,
        ReadingProgress.last_read_at >= year_start,
        ReadingProgress.last_read_at <= year_end
    )

    if series_age_filter is not None:
        busiest_month = busiest_month.filter(series_age_filter)

    busiest_month = busiest_month.group_by('month') \
        .order_by(func.count(func.distinct(ReadingProgress.comic_id)).desc()) \
        .first()

    # Map month number to name
    month_names = ['January', 'February', 'March', 'April', 'May', 'June',
                   'July', 'August', 'September', 'October', 'November', 'December']
    busiest_month_name = month_names[int(busiest_month.month) - 1] if busiest_month else None

    # === LONGEST SERIES COMPLETED ===
    # Find the series with the most issues read in this year
    longest_series = db.query(
        Series.id,
        Series.name,
        func.count(func.distinct(Comic.id)).label('issues_completed')
    ).select_from(Series) \
        .join(Volume, Volume.series_id == Series.id) \
        .join(Comic, Comic.volume_id == Volume.id) \
        .join(ReadingProgress, ReadingProgress.comic_id == Comic.id) \
        .filter(
        ReadingProgress.user_id == current_user.id,
        ReadingProgress.completed == True,
        ReadingProgress.last_read_at >= year_start,
        ReadingProgress.last_read_at <= year_end
    )

    if series_age_filter is not None:
        longest_series = longest_series.filter(series_age_filter)

    longest_series = longest_series.group_by(Series.id, Series.name) \
        .order_by(func.count(func.distinct(Comic.id)).desc()) \
        .first()

    # === READING STREAK ===
    # Find longest consecutive days of reading
    reading_dates = db.query(
        func.date(ReadingProgress.last_read_at).label('read_date')
    ).join(Comic, ReadingProgress.comic_id == Comic.id) \
        .join(Volume).join(Series) \
        .filter(
        ReadingProgress.user_id == current_user.id,
        ReadingProgress.last_read_at >= year_start,
        ReadingProgress.last_read_at <= year_end
    )

    if series_age_filter is not None:
        reading_dates = reading_dates.filter(series_age_filter)

    reading_dates = reading_dates.distinct().order_by('read_date').all()

    # Calculate longest streak
    longest_streak = 0
    current_streak = 0
    prev_date = None

    for row in reading_dates:
        read_date = datetime.strptime(row.read_date, '%Y-%m-%d').date()
        if prev_date is None or (read_date - prev_date).days == 1:
            current_streak += 1
            longest_streak = max(longest_streak, current_streak)
        else:
            current_streak = 1
        prev_date = read_date

    # === CALCULATE FUN COMPARISONS ===
    total_pages = stats.total_pages or 0

    # Average comic is ~22 pages, graphic novel is ~150 pages
    graphic_novel_equivalent = round(total_pages / 150, 1)

    # Reading time (1.25 mins per page = 0.021 hours)
    reading_hours = round(total_pages * 0.021, 1)

    # Days worth of reading (assuming 8 hours per day)
    days_equivalent = round(reading_hours / 8, 1)

    return {
        "year": year,
        "stats": {
            "comics_completed": stats.comics_completed or 0,
            "total_pages": total_pages,
            "series_explored": stats.series_explored or 0,
            "volumes_completed": stats.volumes_completed or 0,
            "reading_hours": reading_hours,
            "graphic_novels_equivalent": graphic_novel_equivalent,
            "days_equivalent": days_equivalent
        },
        "favorites": {
            "top_writer": {
                "name": top_writer.name if top_writer else None,
                "comics_read": top_writer.comics_read if top_writer else 0
            },
            "top_artist": {
                "name": top_artist.name if top_artist else None,
                "comics_read": top_artist.comics_read if top_artist else 0
            },
            "top_series": {
                "name": top_series.name if top_series else None,
                "issues_read": top_series.issues_read if top_series else 0
            },
            "top_genre": {
                "name": top_genre.name if top_genre else None,
                "count": top_genre.count if top_genre else 0
            },
            "top_character": {
                "name": top_character.name if top_character else None,
                "appearances": top_character.appearances if top_character else 0
            }
        },
        "highlights": {
            "busiest_month": {
                "name": busiest_month_name,
                "comics_read": busiest_month.count if busiest_month else 0
            },
            "longest_streak": longest_streak,
            "longest_series_completed": {
                "name": longest_series.name if longest_series else None,
                "issues_completed": longest_series.issues_completed if longest_series else 0
            }
        },
        "fun_facts": {
            "if_this_was_novels": f"That's like reading {graphic_novel_equivalent} graphic novels!",
            "time_spent": f"You spent {reading_hours} hours reading comics this year",
            "marathon": f"That's {days_equivalent} full days of reading!" if days_equivalent >= 1 else f"That's {reading_hours} hours of reading!"
        }
    }



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
