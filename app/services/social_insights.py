from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.comic import Comic, Volume
from app.models.reading_progress import ReadingProgress
from app.models.user import User


MIN_VISIBLE_SOCIAL_COUNT = 2


def _visible_count(count: int) -> int | None:
    return count if count >= MIN_VISIBLE_SOCIAL_COUNT else None


def get_visible_comic_reader_count(db: Session, comic_id: int) -> int | None:
    """
    Return the number of opted-in Parker users who completed this comic.
    Counts below the public threshold are suppressed.
    """
    count = (
        db.query(func.count(func.distinct(ReadingProgress.user_id)))
        .select_from(ReadingProgress)
        .join(User, User.id == ReadingProgress.user_id)
        .filter(ReadingProgress.comic_id == comic_id)
        .filter(ReadingProgress.completed == True)
        .filter(User.share_progress_enabled == True)
        .scalar()
        or 0
    )

    return _visible_count(count)


def get_visible_series_reader_count(db: Session, series_id: int) -> int | None:
    """
    Return the number of opted-in Parker users who are reading or have read
    at least one issue in the series. Counts below the public threshold are
    suppressed.
    """
    count = (
        db.query(func.count(func.distinct(ReadingProgress.user_id)))
        .select_from(ReadingProgress)
        .join(User, User.id == ReadingProgress.user_id)
        .join(Comic, Comic.id == ReadingProgress.comic_id)
        .join(Volume, Volume.id == Comic.volume_id)
        .filter(Volume.series_id == series_id)
        .filter(or_(ReadingProgress.completed == True, ReadingProgress.current_page > 0))
        .filter(User.share_progress_enabled == True)
        .scalar()
        or 0
    )

    return _visible_count(count)
