from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, func

from app.api.deps import CurrentUser, SessionDep
from app.core.comic_helpers import get_series_age_restriction, get_smart_cover, get_thumbnail_url
from app.models.comic import Comic, Volume
from app.models.credits import ComicCredit, Person
from app.models.reading_progress import ReadingProgress
from app.models.series import Series

router = APIRouter()


PERSON_ROLE_ORDER = (
    "writer",
    "penciller",
    "inker",
    "colorist",
    "letterer",
    "cover_artist",
    "editor",
)


def _role_label(role: str) -> str:
    return role.replace("_", " ").title()


def _role_sort_key(role: str) -> tuple[int, str]:
    try:
        return PERSON_ROLE_ORDER.index(role), role
    except ValueError:
        return len(PERSON_ROLE_ORDER), role


def _apply_visible_series_scope(query, current_user: CurrentUser):
    if not current_user.is_superuser:
        allowed_ids = [library.id for library in current_user.accessible_libraries]
        query = query.filter(Series.library_id.in_(allowed_ids))

    age_filter = get_series_age_restriction(current_user)
    if age_filter is not None:
        query = query.filter(age_filter)

    return query


def _person_credit_query(db: SessionDep, person_id: int):
    return (
        db.query(ComicCredit)
        .select_from(ComicCredit)
        .join(Comic, Comic.id == ComicCredit.comic_id)
        .join(Volume, Volume.id == Comic.volume_id)
        .join(Series, Series.id == Volume.series_id)
        .filter(ComicCredit.person_id == person_id)
    )


def _person_role_comics_query(db: SessionDep, person_id: int, role: str, current_user: CurrentUser):
    query = (
        db.query(Comic)
        .join(Volume, Volume.id == Comic.volume_id)
        .join(Series, Series.id == Volume.series_id)
        .join(ComicCredit, ComicCredit.comic_id == Comic.id)
        .filter(
            ComicCredit.person_id == person_id,
            ComicCredit.role == role,
        )
    )
    return _apply_visible_series_scope(query, current_user)


def _get_role_series(db: SessionDep, person_id: int, role: str, current_user: CurrentUser, limit: int) -> list[dict]:
    issue_count = func.count(func.distinct(Comic.id)).label("issue_count")
    read_count = func.count(func.distinct(ReadingProgress.id)).label("read_count")

    query = (
        db.query(
            Series.id.label("id"),
            Series.name.label("name"),
            issue_count,
            read_count,
            func.min(Comic.year).label("start_year"),
            func.max(Comic.year).label("end_year"),
            func.max(Comic.publisher).label("publisher"),
        )
        .select_from(Series)
        .join(Volume, Volume.series_id == Series.id)
        .join(Comic, Comic.volume_id == Volume.id)
        .join(ComicCredit, ComicCredit.comic_id == Comic.id)
        .outerjoin(
            ReadingProgress,
            and_(
                ReadingProgress.comic_id == Comic.id,
                ReadingProgress.user_id == current_user.id,
                ReadingProgress.completed == True,
            ),
        )
        .filter(
            ComicCredit.person_id == person_id,
            ComicCredit.role == role,
        )
    )
    query = _apply_visible_series_scope(query, current_user)
    rows = (
        query
        .group_by(Series.id, Series.name)
        .order_by(issue_count.desc(), Series.name.asc())
        .limit(limit)
        .all()
    )

    items = []
    for row in rows:
        cover_query = _person_role_comics_query(db, person_id, role, current_user).filter(Series.id == row.id)
        cover = get_smart_cover(cover_query, series_name=row.name)
        issue_total = int(row.issue_count or 0)
        read_total = int(row.read_count or 0)
        items.append(
            {
                "id": row.id,
                "name": row.name,
                "issue_count": issue_total,
                "start_year": row.start_year,
                "end_year": row.end_year,
                "publisher": row.publisher,
                "thumbnail_path": get_thumbnail_url(cover.id, cover.updated_at) if cover else None,
                "read": issue_total > 0 and read_total >= issue_total,
            }
        )

    return items


@router.get("/{person_id}", name="detail")
async def get_person_detail(
    person_id: int,
    db: SessionDep,
    current_user: CurrentUser,
    role_limit: Annotated[int, Query(ge=1, le=24)] = 12,
):
    person = db.get(Person, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")

    visible_credit_query = _apply_visible_series_scope(_person_credit_query(db, person_id), current_user)

    total_stats = (
        visible_credit_query
        .with_entities(
            func.count(func.distinct(Comic.id)).label("issue_count"),
            func.count(func.distinct(Series.id)).label("series_count"),
            func.min(Comic.year).label("start_year"),
            func.max(Comic.year).label("end_year"),
        )
        .first()
    )

    total_issues = int(total_stats.issue_count or 0)
    if total_issues <= 0:
        raise HTTPException(status_code=404, detail="Person not found")

    role_issue_count = func.count(func.distinct(Comic.id)).label("issue_count")
    role_rows = (
        visible_credit_query
        .with_entities(
            ComicCredit.role.label("role"),
            role_issue_count,
            func.count(func.distinct(Series.id)).label("series_count"),
            func.min(Comic.year).label("start_year"),
            func.max(Comic.year).label("end_year"),
        )
        .group_by(ComicCredit.role)
        .all()
    )

    publisher_issue_count = func.count(func.distinct(Comic.id)).label("issue_count")
    publisher_rows = (
        visible_credit_query
        .with_entities(Comic.publisher.label("name"), publisher_issue_count)
        .filter(Comic.publisher != None, Comic.publisher != "")
        .group_by(Comic.publisher)
        .order_by(publisher_issue_count.desc(), Comic.publisher.asc())
        .limit(5)
        .all()
    )

    roles = []
    for row in sorted(role_rows, key=lambda role_row: _role_sort_key(role_row.role)):
        roles.append(
            {
                "role": row.role,
                "label": _role_label(row.role),
                "issue_count": int(row.issue_count or 0),
                "series_count": int(row.series_count or 0),
                "start_year": row.start_year,
                "end_year": row.end_year,
                "series": _get_role_series(db, person_id, row.role, current_user, role_limit),
            }
        )

    return {
        "id": person.id,
        "name": person.name,
        "total_issues": total_issues,
        "total_series": int(total_stats.series_count or 0),
        "start_year": total_stats.start_year,
        "end_year": total_stats.end_year,
        "roles": roles,
        "top_publishers": [
            {"name": row.name, "issue_count": int(row.issue_count or 0)}
            for row in publisher_rows
        ],
    }
