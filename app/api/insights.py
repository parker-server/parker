from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc, func
from sqlalchemy.orm import aliased

from app.api.deps import CurrentUser, SessionDep
from app.core.comic_helpers import get_series_age_restriction
from app.models.comic import Comic, Volume
from app.models.credits import ComicCredit, Person
from app.models.series import Series
from app.models.tags import Character, comic_characters

router = APIRouter()

CreatorRole = Literal["writer", "penciller", "inker", "colorist", "letterer", "editor", "cover_artist"]


def _ensure_library_access(library_id: int, user: CurrentUser) -> None:
    if user.is_superuser:
        return

    allowed_ids = {lib.id for lib in user.accessible_libraries}
    if library_id not in allowed_ids:
        raise HTTPException(status_code=404, detail="Library not found")


def _empty_matrix_payload(**payload):
    return {
        **payload,
        "pair_count": 0,
        "max_shared_issues": 0,
        "rows": [],
        "columns": [],
        "pairs": [],
        "top_collaborations": [],
    }


def _serialize_visible_matrix(pairs, limit: int):
    row_totals = {}
    col_totals = {}
    for pair in pairs:
        row_key = (pair.person_a_id, pair.person_a)
        col_key = (pair.person_b_id, pair.person_b)
        row_totals[row_key] = row_totals.get(row_key, 0) + pair.shared_issues
        col_totals[col_key] = col_totals.get(col_key, 0) + pair.shared_issues

    row_rankings = [
        {"id": person_id, "name": name, "total_shared": total_shared}
        for (person_id, name), total_shared in sorted(
            row_totals.items(),
            key=lambda item: (-item[1], item[0][1].lower(), item[0][0]),
        )[:limit]
    ]
    column_rankings = [
        {"id": person_id, "name": name, "total_shared": total_shared}
        for (person_id, name), total_shared in sorted(
            col_totals.items(),
            key=lambda item: (-item[1], item[0][1].lower(), item[0][0]),
        )[:limit]
    ]

    row_id_set = {entry["id"] for entry in row_rankings}
    column_id_set = {entry["id"] for entry in column_rankings}
    filtered_pairs = [
        pair for pair in pairs if pair.person_a_id in row_id_set and pair.person_b_id in column_id_set
    ]

    if not filtered_pairs:
        top_pair = pairs[0]
        row_rankings = [{"id": top_pair.person_a_id, "name": top_pair.person_a, "total_shared": top_pair.shared_issues}]
        column_rankings = [{"id": top_pair.person_b_id, "name": top_pair.person_b, "total_shared": top_pair.shared_issues}]
        filtered_pairs = [top_pair]

    visible_row_totals = {}
    visible_col_totals = {}
    for pair in filtered_pairs:
        visible_row_totals[pair.person_a_id] = visible_row_totals.get(pair.person_a_id, 0) + pair.shared_issues
        visible_col_totals[pair.person_b_id] = visible_col_totals.get(pair.person_b_id, 0) + pair.shared_issues

    row_entries = [
        {
            "id": entry["id"],
            "name": entry["name"],
            "total_shared": visible_row_totals.get(entry["id"], 0),
        }
        for entry in row_rankings
    ]
    column_entries = [
        {
            "id": entry["id"],
            "name": entry["name"],
            "total_shared": visible_col_totals.get(entry["id"], 0),
        }
        for entry in column_rankings
    ]

    max_shared_issues = max(pair.shared_issues for pair in filtered_pairs)

    serialized_pairs = [
        {
            "person_a_id": pair.person_a_id,
            "person_a": pair.person_a,
            "person_b_id": pair.person_b_id,
            "person_b": pair.person_b,
            "shared_issues": pair.shared_issues,
            "shared_series": pair.shared_series,
            "sample_series": pair.sample_series,
            "intensity": round(pair.shared_issues / max_shared_issues, 4) if max_shared_issues else 0,
        }
        for pair in filtered_pairs
    ]

    top_collaborations = sorted(
        serialized_pairs,
        key=lambda item: (-item["shared_issues"], item["person_a"].lower(), item["person_b"].lower()),
    )[:10]

    return {
        "pair_count": len(serialized_pairs),
        "max_shared_issues": max_shared_issues,
        "rows": row_entries,
        "columns": column_entries,
        "pairs": serialized_pairs,
        "top_collaborations": top_collaborations,
    }


@router.get("/creator-collaborations", name="creator_collaborations")
async def get_creator_collaborations(
    db: SessionDep,
    current_user: CurrentUser,
    role_a: CreatorRole = Query("writer"),
    role_b: CreatorRole = Query("penciller"),
    library_id: Optional[int] = Query(None, ge=1),
    min_shared: int = Query(2, ge=1, le=100),
    limit: int = Query(12, ge=2, le=15, description="Maximum creators shown per axis"),
):
    """
    Aggregates creator-to-creator collaboration pairs for heatmap-style insights.
    """
    if library_id is not None:
        _ensure_library_access(library_id, current_user)

    credit_a = aliased(ComicCredit)
    credit_b = aliased(ComicCredit)
    person_a = aliased(Person)
    person_b = aliased(Person)

    shared_issue_count = func.count(func.distinct(Comic.id))
    shared_series_count = func.count(func.distinct(Series.id))

    query = (
        db.query(
            person_a.id.label("person_a_id"),
            person_a.name.label("person_a"),
            person_b.id.label("person_b_id"),
            person_b.name.label("person_b"),
            shared_issue_count.label("shared_issues"),
            shared_series_count.label("shared_series"),
            func.min(Series.name).label("sample_series"),
        )
        .select_from(Comic)
        .join(Volume, Comic.volume_id == Volume.id)
        .join(Series, Volume.series_id == Series.id)
        .join(credit_a, credit_a.comic_id == Comic.id)
        .join(person_a, person_a.id == credit_a.person_id)
        .join(credit_b, credit_b.comic_id == Comic.id)
        .join(person_b, person_b.id == credit_b.person_id)
        .filter(credit_a.role == role_a, credit_b.role == role_b)
    )

    if role_a == role_b:
        query = query.filter(credit_a.person_id < credit_b.person_id)

    if not current_user.is_superuser:
        allowed_ids = [lib.id for lib in current_user.accessible_libraries]
        query = query.filter(Series.library_id.in_(allowed_ids))

    if library_id is not None:
        query = query.filter(Series.library_id == library_id)

    age_filter = get_series_age_restriction(current_user)
    if age_filter is not None:
        query = query.filter(age_filter)

    pair_cap = max(limit * limit * 2, 50)

    pairs = (
        query.group_by(person_a.id, person_a.name, person_b.id, person_b.name)
        .having(shared_issue_count >= min_shared)
        .order_by(desc("shared_issues"), person_a.name.asc(), person_b.name.asc())
        .limit(pair_cap)
        .all()
    )

    if not pairs:
        return _empty_matrix_payload(
            role_a=role_a,
            role_b=role_b,
            library_id=library_id,
            min_shared=min_shared,
            limit=limit,
        )

    return {
        "role_a": role_a,
        "role_b": role_b,
        "library_id": library_id,
        "min_shared": min_shared,
        "limit": limit,
        **_serialize_visible_matrix(pairs, limit),
    }


@router.get("/character-collaborations", name="character_collaborations")
async def get_character_collaborations(
    db: SessionDep,
    current_user: CurrentUser,
    library_id: Optional[int] = Query(None, ge=1),
    min_shared: int = Query(2, ge=1, le=100),
    limit: int = Query(12, ge=2, le=15, description="Maximum characters shown per axis"),
):
    """
    Aggregates character co-appearance pairs for heatmap-style insights.
    """
    if library_id is not None:
        _ensure_library_access(library_id, current_user)

    char_link_a = comic_characters.alias("char_link_a")
    char_link_b = comic_characters.alias("char_link_b")
    character_a = aliased(Character)
    character_b = aliased(Character)

    shared_issue_count = func.count(func.distinct(Comic.id))
    shared_series_count = func.count(func.distinct(Series.id))

    query = (
        db.query(
            character_a.id.label("person_a_id"),
            character_a.name.label("person_a"),
            character_b.id.label("person_b_id"),
            character_b.name.label("person_b"),
            shared_issue_count.label("shared_issues"),
            shared_series_count.label("shared_series"),
            func.min(Series.name).label("sample_series"),
        )
        .select_from(Comic)
        .join(Volume, Comic.volume_id == Volume.id)
        .join(Series, Volume.series_id == Series.id)
        .join(char_link_a, char_link_a.c.comic_id == Comic.id)
        .join(character_a, character_a.id == char_link_a.c.character_id)
        .join(char_link_b, char_link_b.c.comic_id == Comic.id)
        .join(character_b, character_b.id == char_link_b.c.character_id)
        .filter(character_a.id < character_b.id)
    )

    if not current_user.is_superuser:
        allowed_ids = [lib.id for lib in current_user.accessible_libraries]
        query = query.filter(Series.library_id.in_(allowed_ids))

    if library_id is not None:
        query = query.filter(Series.library_id == library_id)

    age_filter = get_series_age_restriction(current_user)
    if age_filter is not None:
        query = query.filter(age_filter)

    pair_cap = max(limit * limit * 2, 50)

    pairs = (
        query.group_by(character_a.id, character_a.name, character_b.id, character_b.name)
        .having(shared_issue_count >= min_shared)
        .order_by(desc("shared_issues"), character_a.name.asc(), character_b.name.asc())
        .limit(pair_cap)
        .all()
    )

    if not pairs:
        return _empty_matrix_payload(
            library_id=library_id,
            min_shared=min_shared,
            limit=limit,
        )

    return {
        "library_id": library_id,
        "min_shared": min_shared,
        "limit": limit,
        **_serialize_visible_matrix(pairs, limit),
    }
