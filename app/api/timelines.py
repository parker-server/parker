from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import Float, case, cast, func, or_
from sqlalchemy.orm import joinedload

from app.api.deps import CurrentUser, SessionDep
from app.core.comic_helpers import get_series_age_restriction, get_thumbnail_url
from app.models.collection import Collection, CollectionItem
from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.series import Series
from app.models.tags import Character, Team

router = APIRouter()

TimelineSubjectType = Literal["character", "team"]

SUBJECT_MODELS = {
    "character": (Character, Comic.characters),
    "team": (Team, Comic.teams),
}


def _allowed_library_ids(user: CurrentUser) -> list[int] | None:
    if user.is_superuser:
        return None
    return [library.id for library in user.accessible_libraries]


def _apply_scope(query, user: CurrentUser, allowed_ids: list[int] | None):
    if allowed_ids is not None:
        query = query.filter(Series.library_id.in_(allowed_ids))

    age_filter = get_series_age_restriction(user)
    if age_filter is not None:
        query = query.filter(age_filter)

    return query


def _subject_query(db: SessionDep, subject_type: TimelineSubjectType, name: str, user: CurrentUser):
    model, relationship = SUBJECT_MODELS[subject_type]
    allowed_ids = _allowed_library_ids(user)

    query = (
        db.query(model)
        .join(model.comics)
        .join(Volume, Comic.volume_id == Volume.id)
        .join(Series, Volume.series_id == Series.id)
        .filter(model.name == name)
    )

    return _apply_scope(query, user, allowed_ids).distinct()


def _valid_year(comic: Comic) -> int | None:
    return comic.year if comic.year and comic.year > 0 else None


def _valid_month(comic: Comic) -> int | None:
    return comic.month if comic.month and comic.month > 0 else None


def _valid_day(comic: Comic) -> int | None:
    return comic.day if comic.day and comic.day > 0 else None


def _date_label(comic: Comic) -> str | None:
    year = _valid_year(comic)
    if year is None:
        return None

    month = _valid_month(comic)
    day = _valid_day(comic)
    if month is None:
        return str(year)
    if day is None:
        return f"{year}-{month:02d}"
    return f"{year}-{month:02d}-{day:02d}"


def _comic_sort_key(comic: Comic):
    year = _valid_year(comic) or 9999
    month = _valid_month(comic) or 99
    day = _valid_day(comic) or 99
    try:
        issue_number = float(comic.number)
    except (TypeError, ValueError):
        issue_number = 999999.0

    series_name = comic.volume.series.name if comic.volume and comic.volume.series else ""
    return (year, month, day, series_name.lower(), issue_number, comic.number or "", comic.id)


def _serialize_comic(comic: Comic, annotations: dict[int, dict]) -> dict:
    series = comic.volume.series
    comic_annotations = annotations.get(comic.id, {})

    return {
        "id": comic.id,
        "series_id": series.id,
        "series": series.name,
        "volume": comic.volume.volume_number,
        "number": comic.number,
        "title": comic.title,
        "year": _valid_year(comic),
        "month": _valid_month(comic),
        "day": _valid_day(comic),
        "date_label": _date_label(comic),
        "publisher": comic.publisher,
        "imprint": comic.imprint,
        "format": comic.format,
        "story_arc": comic.story_arc,
        "series_group": comic.series_group,
        "thumbnail_path": get_thumbnail_url(comic.id, comic.updated_at),
        "reading_lists": comic_annotations.get("reading_lists", []),
        "collections": comic_annotations.get("collections", []),
    }


def _load_annotations(db: SessionDep, comics: list[Comic]) -> dict[int, dict]:
    comic_ids = [comic.id for comic in comics]
    annotations = {
        comic_id: {"reading_lists": [], "collections": []}
        for comic_id in comic_ids
    }
    if not comic_ids:
        return annotations

    reading_rows = (
        db.query(
            ReadingListItem.comic_id,
            ReadingList.id,
            ReadingList.name,
            ReadingListItem.position,
        )
        .join(ReadingList, ReadingListItem.reading_list_id == ReadingList.id)
        .join(Comic, ReadingListItem.comic_id == Comic.id)
        .join(Volume, Comic.volume_id == Volume.id)
        .join(Series, Volume.series_id == Series.id)
        .join(Library, Series.library_id == Library.id)
        .filter(ReadingListItem.comic_id.in_(comic_ids), Library.parse_reading_lists == True)
        .order_by(ReadingList.name.asc(), ReadingListItem.position.asc())
        .all()
    )

    collection_rows = (
        db.query(CollectionItem.comic_id, Collection.id, Collection.name)
        .join(Collection, CollectionItem.collection_id == Collection.id)
        .join(Comic, CollectionItem.comic_id == Comic.id)
        .join(Volume, Comic.volume_id == Volume.id)
        .join(Series, Volume.series_id == Series.id)
        .join(Library, Series.library_id == Library.id)
        .filter(CollectionItem.comic_id.in_(comic_ids), Library.parse_collections == True)
        .order_by(Collection.name.asc())
        .all()
    )

    for comic_id, list_id, list_name, position in reading_rows:
        annotations[comic_id]["reading_lists"].append(
            {"id": list_id, "name": list_name, "position": position}
        )

    for comic_id, collection_id, collection_name in collection_rows:
        annotations[comic_id]["collections"].append(
            {"id": collection_id, "name": collection_name}
        )

    return annotations


def _first_by(items: list[Comic], key_fn) -> list[dict]:
    first_items = {}
    for comic in items:
        key = key_fn(comic)
        if not key:
            continue
        if key not in first_items:
            first_items[key] = comic

    return [
        {"name": name, "comic": comic}
        for name, comic in sorted(first_items.items(), key=lambda item: item[0].lower())
    ]


@router.get("/suggestions", name="suggestions")
async def timeline_suggestions(
    db: SessionDep,
    current_user: CurrentUser,
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=25),
):
    q_str = f"%{q}%"
    allowed_ids = _allowed_library_ids(current_user)
    suggestions = []

    for subject_type, (model, _relationship) in SUBJECT_MODELS.items():
        query = (
            db.query(model.name)
            .join(model.comics)
            .join(Volume, Comic.volume_id == Volume.id)
            .join(Series, Volume.series_id == Series.id)
            .filter(model.name.ilike(q_str))
        )
        rows = (
            _apply_scope(query, current_user, allowed_ids)
            .distinct()
            .order_by(model.name.asc())
            .limit(limit)
            .all()
        )
        suggestions.extend(
            {"type": subject_type, "name": row[0]}
            for row in rows
            if row[0]
        )

    return sorted(suggestions, key=lambda item: (item["name"].lower(), item["type"]))[:limit]


@router.get("/", name="detail")
async def get_timeline(
    db: SessionDep,
    current_user: CurrentUser,
    subject_type: TimelineSubjectType = Query(..., alias="type"),
    name: str = Query(..., min_length=1),
    per_year_limit: int = Query(8, ge=1, le=25),
):
    subject = _subject_query(db, subject_type, name, current_user).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Timeline subject not found")

    model, relationship = SUBJECT_MODELS[subject_type]
    allowed_ids = _allowed_library_ids(current_user)

    sort_year = case((or_(Comic.year == None, Comic.year <= 0), 9999), else_=Comic.year)
    sort_month = case((or_(Comic.month == None, Comic.month <= 0), 99), else_=Comic.month)
    sort_day = case((or_(Comic.day == None, Comic.day <= 0), 99), else_=Comic.day)

    query = (
        db.query(Comic)
        .join(Volume, Comic.volume_id == Volume.id)
        .join(Series, Volume.series_id == Series.id)
        .filter(relationship.any(model.name == subject.name))
        .options(joinedload(Comic.volume).joinedload(Volume.series))
    )
    comics = (
        _apply_scope(query, current_user, allowed_ids)
        .order_by(
            sort_year.asc(),
            sort_month.asc(),
            sort_day.asc(),
            Series.name.asc(),
            cast(Comic.number, Float).asc(),
            Comic.number.asc(),
        )
        .all()
    )

    comics = sorted(comics, key=_comic_sort_key)
    dated_comics = [comic for comic in comics if _valid_year(comic) is not None]
    undated_comics = [comic for comic in comics if _valid_year(comic) is None]
    annotations = _load_annotations(db, comics)

    years = []
    for year in sorted({comic.year for comic in dated_comics}):
        year_comics = [comic for comic in dated_comics if comic.year == year]
        visible = year_comics[:per_year_limit]
        years.append(
            {
                "year": year,
                "issue_count": len(year_comics),
                "hidden_count": max(len(year_comics) - len(visible), 0),
                "entries": [_serialize_comic(comic, annotations) for comic in visible],
            }
        )

    first_issue = dated_comics[0] if dated_comics else (comics[0] if comics else None)
    latest_issue = dated_comics[-1] if dated_comics else (comics[-1] if comics else None)

    first_story_arcs = _first_by(
        dated_comics,
        lambda comic: comic.story_arc.strip() if comic.story_arc and comic.story_arc.strip() else None,
    )
    first_series = _first_by(
        dated_comics or comics,
        lambda comic: comic.volume.series.name if comic.volume and comic.volume.series else None,
    )

    reading_list_firsts = {}
    collection_firsts = {}
    for comic in dated_comics:
        for reading_list in annotations.get(comic.id, {}).get("reading_lists", []):
            reading_list_firsts.setdefault(reading_list["name"], {"id": reading_list["id"], "comic": comic})
        for collection in annotations.get(comic.id, {}).get("collections", []):
            collection_firsts.setdefault(collection["name"], {"id": collection["id"], "comic": comic})

    year_values = [_valid_year(comic) for comic in dated_comics]
    year_values = [year for year in year_values if year is not None]

    return {
        "subject": {
            "type": subject_type,
            "name": subject.name,
        },
        "summary": {
            "total_issues": len(comics),
            "dated_issues": len(dated_comics),
            "undated_issues": len(undated_comics),
            "series_count": len({comic.volume.series_id for comic in comics}),
            "story_arc_count": len(
                {
                    comic.story_arc.strip()
                    for comic in comics
                    if comic.story_arc and comic.story_arc.strip()
                }
            ),
            "reading_list_count": len(reading_list_firsts),
            "collection_count": len(collection_firsts),
            "start_year": min(year_values) if year_values else None,
            "end_year": max(year_values) if year_values else None,
            "per_year_limit": per_year_limit,
        },
        "milestones": {
            "first_issue": _serialize_comic(first_issue, annotations) if first_issue else None,
            "latest_issue": _serialize_comic(latest_issue, annotations) if latest_issue else None,
            "first_series": [
                {"name": item["name"], "comic": _serialize_comic(item["comic"], annotations)}
                for item in first_series[:12]
            ],
            "first_story_arcs": [
                {"name": item["name"], "comic": _serialize_comic(item["comic"], annotations)}
                for item in first_story_arcs[:12]
            ],
            "first_reading_lists": [
                {"id": item["id"], "name": name, "comic": _serialize_comic(item["comic"], annotations)}
                for name, item in sorted(reading_list_firsts.items(), key=lambda item: item[0].lower())[:12]
            ],
            "first_collections": [
                {"id": item["id"], "name": name, "comic": _serialize_comic(item["comic"], annotations)}
                for name, item in sorted(collection_firsts.items(), key=lambda item: item[0].lower())[:12]
            ],
        },
        "years": years,
        "undated_entries": [_serialize_comic(comic, annotations) for comic in undated_comics[:25]],
        "undated_hidden_count": max(len(undated_comics) - 25, 0),
    }
