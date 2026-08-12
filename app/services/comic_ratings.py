from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.interactions import UserComicRating


def recompute_parker_rating_summary(db: Session, comic_id: int) -> dict:
    """
    Compute the current Parker aggregate for a comic.
    This is the single place to evolve later if we add cached columns.
    """
    average, count = (
        db.query(
            func.avg(UserComicRating.rating),
            func.count(UserComicRating.user_id),
        )
        .filter(UserComicRating.comic_id == comic_id)
        .one()
    )

    return {
        "parker_rating_average": float(average) if average is not None else None,
        "parker_rating_count": int(count or 0),
    }


def get_user_comic_rating(db: Session, comic_id: int, user_id: int) -> int | None:
    return (
        db.query(UserComicRating.rating)
        .filter(
            UserComicRating.comic_id == comic_id,
            UserComicRating.user_id == user_id,
        )
        .scalar()
    )


def build_parker_rating_state(db: Session, comic_id: int, user_id: int | None) -> dict:
    state = recompute_parker_rating_summary(db, comic_id)
    state["user_rating"] = get_user_comic_rating(db, comic_id, user_id) if user_id is not None else None
    return state
