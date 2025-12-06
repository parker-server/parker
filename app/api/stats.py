from fastapi import APIRouter, Depends
from sqlalchemy import func, case, desc
from typing import Annotated

from app.api.deps import SessionDep, AdminUser
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.library import Library
from app.models.tags import Genre, comic_genres
from app.models.user import User
from app.models.reading_progress import ReadingProgress

router = APIRouter()


@router.get("/")
async def get_system_stats(
        db: SessionDep,
        admin: AdminUser
):
    """
    Get global server statistics.
    """
    # 1. Basic Counts
    library_count = db.query(Library).count()
    series_count = db.query(Series).count()
    volume_count = db.query(Volume).count()
    comic_count = db.query(Comic).count()
    user_count = db.query(User).count()

    # 2. Storage Usage (Sum of file_size column)
    # Result is in Bytes
    total_size_bytes = db.query(func.sum(Comic.file_size)).scalar() or 0

    # 3. Reading Activity
    total_read_pages = db.query(func.sum(ReadingProgress.current_page)).scalar() or 0
    completed_books = db.query(ReadingProgress).filter(ReadingProgress.completed == True).count()

    return {
        "counts": {
            "libraries": library_count,
            "series": series_count,
            "volumes": volume_count,
            "comics": comic_count,
            "users": user_count
        },
        "storage": {
            "total_bytes": total_size_bytes
        },
        "activity": {
            "pages_read": total_read_pages,
            "completed_books": completed_books
        }
    }

@router.get("/genres")
async def get_genre_stats(db: SessionDep, user: AdminUser):
    """
    Returns aggregated stats per genre:
    - Inventory (Total Comics)
    - Consumption (Read Percentage)
    - Storage (Total Bytes)
    """
    stats = (
        db.query(
            Genre.name,
            func.count(Comic.id).label("total_count"),
            # Count how many of these comics have been completed by the specific user
            func.sum(case((ReadingProgress.completed == True, 1), else_=0)).label("read_count"),
            func.sum(Comic.file_size).label("total_bytes")
        )
        .join(comic_genres, comic_genres.c.genre_id == Genre.id)
        .join(Comic, comic_genres.c.comic_id == Comic.id)
        .outerjoin(
            ReadingProgress,
            (ReadingProgress.comic_id == Comic.id) & (ReadingProgress.user_id == user.id)
        )
        .group_by(Genre.name)
        .order_by(desc("total_count"))
        .limit(15) # Top 15 genres by volume
        .all()
    )

    return [
        {
            "genre": row.name,
            "inventory": row.total_count,
            "read_count": row.read_count or 0,
            "read_pct": round((row.read_count / row.total_count) * 100, 1) if row.total_count and row.read_count else 0,
            "size_bytes": row.total_bytes or 0
        }
        for row in stats
    ]