from typing import Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.comic import Comic
from app.models.credits import ComicCredit, Person
from app.models.tags import (
    Character,
    Location,
    Team,
    comic_characters,
    comic_locations,
    comic_teams,
)

VOLUME_METADATA_CATEGORIES = ("writers", "pencillers", "characters", "teams", "locations")
VOLUME_METADATA_PAGE_SIZE = 25


def get_volume_metadata_tags_page(
    db: Session,
    volume_ids: Sequence[int],
    category: str,
    *,
    offset: int = 0,
    limit: int = VOLUME_METADATA_PAGE_SIZE,
) -> dict:
    if not volume_ids:
        return _empty_page(category, offset, limit)

    query, count_expr, name_column = _metadata_tag_query(db, volume_ids, category)
    total = db.query(func.count()).select_from(query.subquery()).scalar() or 0

    rows = (
        query
        .order_by(count_expr.desc(), name_column.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = [
        {"name": row.name, "count": int(row.appearance_count or 0)}
        for row in rows
    ]

    return {
        "category": category,
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(items) < total,
    }


def _metadata_tag_query(db: Session, volume_ids: Sequence[int], category: str):
    count_expr = func.count(func.distinct(Comic.id)).label("appearance_count")

    if category == "writers":
        return (
            db.query(Person.name.label("name"), count_expr)
            .select_from(Person)
            .join(ComicCredit, ComicCredit.person_id == Person.id)
            .join(Comic, Comic.id == ComicCredit.comic_id)
            .filter(Comic.volume_id.in_(volume_ids), ComicCredit.role == "writer")
            .group_by(Person.name),
            count_expr,
            Person.name,
        )

    if category == "pencillers":
        return (
            db.query(Person.name.label("name"), count_expr)
            .select_from(Person)
            .join(ComicCredit, ComicCredit.person_id == Person.id)
            .join(Comic, Comic.id == ComicCredit.comic_id)
            .filter(Comic.volume_id.in_(volume_ids), ComicCredit.role == "penciller")
            .group_by(Person.name),
            count_expr,
            Person.name,
        )

    if category == "characters":
        return (
            db.query(Character.name.label("name"), count_expr)
            .select_from(Character)
            .join(comic_characters, comic_characters.c.character_id == Character.id)
            .join(Comic, Comic.id == comic_characters.c.comic_id)
            .filter(Comic.volume_id.in_(volume_ids))
            .group_by(Character.name),
            count_expr,
            Character.name,
        )

    if category == "teams":
        return (
            db.query(Team.name.label("name"), count_expr)
            .select_from(Team)
            .join(comic_teams, comic_teams.c.team_id == Team.id)
            .join(Comic, Comic.id == comic_teams.c.comic_id)
            .filter(Comic.volume_id.in_(volume_ids))
            .group_by(Team.name),
            count_expr,
            Team.name,
        )

    if category == "locations":
        return (
            db.query(Location.name.label("name"), count_expr)
            .select_from(Location)
            .join(comic_locations, comic_locations.c.location_id == Location.id)
            .join(Comic, Comic.id == comic_locations.c.comic_id)
            .filter(Comic.volume_id.in_(volume_ids))
            .group_by(Location.name),
            count_expr,
            Location.name,
        )

    raise ValueError(f"Unsupported metadata detail category: {category}")


def _empty_page(category: str, offset: int, limit: int) -> dict:
    return {
        "category": category,
        "items": [],
        "total": 0,
        "offset": offset,
        "limit": limit,
        "has_more": False,
    }
