from collections import OrderedDict
from threading import Lock
from typing import Callable, Literal, NamedTuple, Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session, aliased

from app.api.deps import CurrentUser, SessionDep
from app.core.comic_helpers import get_series_age_restriction
from app.models.comic import Comic, Volume
from app.models.credits import ComicCredit, Person
from app.models.job import JobType, ScanJob
from app.models.series import Series
from app.models.tags import Character, comic_characters

router = APIRouter()

CreatorRole = Literal["writer", "penciller", "inker", "colorist", "letterer", "editor", "cover_artist"]

# Pair counting starts with only the most frequent people or characters. A pair can
# never share more issues than its less frequent member appears in, so once the busiest
# left-out entity appears in fewer issues than the weakest pair kept, the result is
# exact. Otherwise the candidate set grows and the count runs again.
PAIR_CANDIDATES_START = 150
PAIR_CANDIDATES_GROWTH = 4
PAIR_CANDIDATES_MAX = 2400

# Results only change when library data changes, so they are cached per user scope
# and keyed on a stamp that moves whenever a scan, cleanup or rehydrate finishes.
_PAYLOAD_CACHE_SIZE = 64
_payload_cache: OrderedDict[tuple, dict] = OrderedDict()
_payload_cache_lock = Lock()
_DATA_CHANGING_JOBS = (JobType.SCAN.value, JobType.CLEANUP.value, JobType.METADATA_REHYDRATE.value)


class PairRow(NamedTuple):
    person_a_id: int
    person_a: str
    person_b_id: int
    person_b: str
    shared_issues: int


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


def _serialize_visible_matrix(
    pairs: list[PairRow],
    limit: int,
    load_series_stats: Callable[[list[PairRow]], dict[tuple[int, int], tuple]],
):
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
    series_stats = load_series_stats(filtered_pairs)

    serialized_pairs = []
    for pair in filtered_pairs:
        shared_series, sample_series = series_stats.get((pair.person_a_id, pair.person_b_id), (0, None))
        serialized_pairs.append({
            "person_a_id": pair.person_a_id,
            "person_a": pair.person_a,
            "person_b_id": pair.person_b_id,
            "person_b": pair.person_b,
            "shared_issues": pair.shared_issues,
            "shared_series": shared_series,
            "sample_series": sample_series,
            "intensity": round(pair.shared_issues / max_shared_issues, 4) if max_shared_issues else 0,
        })

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


def clear_insights_cache() -> None:
    with _payload_cache_lock:
        _payload_cache.clear()


def _data_stamp(db: Session) -> tuple:
    last_job_finished = (
        db.query(func.max(ScanJob.completed_at))
        .filter(ScanJob.job_type.in_(_DATA_CHANGING_JOBS))
        .scalar()
    )
    comic_count = db.query(func.count(Comic.id)).scalar()
    return (last_job_finished, comic_count)


def _user_scope(user: CurrentUser) -> tuple:
    if user.is_superuser:
        return ("superuser",)
    allowed_ids = tuple(sorted(lib.id for lib in user.accessible_libraries))
    return (allowed_ids, user.max_age_rating, user.allow_unknown_age_ratings)


def _cached_payload(db: Session, user: CurrentUser, key: tuple, build: Callable[[], dict]) -> dict:
    cache_key = (key, _user_scope(user), _data_stamp(db))
    with _payload_cache_lock:
        if cache_key in _payload_cache:
            _payload_cache.move_to_end(cache_key)
            return _payload_cache[cache_key]

    payload = build()

    with _payload_cache_lock:
        _payload_cache[cache_key] = payload
        while len(_payload_cache) > _PAYLOAD_CACHE_SIZE:
            _payload_cache.popitem(last=False)
    return payload


def _visible_comic_ids(user: CurrentUser, library_id: Optional[int]) -> Optional[Select]:
    """Comic ids this request may count, or None when every comic counts."""
    age_filter = get_series_age_restriction(user)
    if user.is_superuser and library_id is None and age_filter is None:
        return None

    query = (
        select(Comic.id)
        .join(Volume, Comic.volume_id == Volume.id)
        .join(Series, Volume.series_id == Series.id)
    )
    if not user.is_superuser:
        query = query.where(Series.library_id.in_([lib.id for lib in user.accessible_libraries]))
    if library_id is not None:
        query = query.where(Series.library_id == library_id)
    if age_filter is not None:
        query = query.where(age_filter)
    return query


def _credit_links(role: str, visible_comic_ids: Optional[Select]) -> Select:
    """(comic_id, entity_id) rows for one credit role: one row per comic and person."""
    query = select(
        ComicCredit.comic_id.label("comic_id"),
        ComicCredit.person_id.label("entity_id"),
    ).where(ComicCredit.role == role)
    if visible_comic_ids is not None:
        query = query.where(ComicCredit.comic_id.in_(visible_comic_ids))
    return query


def _character_links(visible_comic_ids: Optional[Select]) -> Select:
    """(comic_id, entity_id) rows for character appearances: one row per comic and character."""
    query = select(
        comic_characters.c.comic_id.label("comic_id"),
        comic_characters.c.character_id.label("entity_id"),
    )
    if visible_comic_ids is not None:
        query = query.where(comic_characters.c.comic_id.in_(visible_comic_ids))
    return query


def _restrict_links(links: Select, entity_ids: Optional[list[int]]) -> Select:
    if entity_ids is None:
        return links
    # "+ 0" keeps SQLite from looking up every listed id in the (comic_id, entity) index
    # for each comic. It reads the comic's few links instead and filters them.
    return links.where((links.selected_columns.entity_id + 0).in_(entity_ids))


def _entity_frequencies(db: Session, links: Select) -> list[tuple[int, int]]:
    """(entity_id, issue_count) for every entity, busiest first."""
    link = links.subquery()
    issue_count = func.count()
    rows = db.execute(
        select(link.c.entity_id, issue_count)
        .group_by(link.c.entity_id)
        .order_by(issue_count.desc(), link.c.entity_id)
    )
    return [(row[0], row[1]) for row in rows]


def _count_top_pairs(
    db: Session,
    links_a: Select,
    links_b: Select,
    entity_a,
    entity_b,
    same_side: bool,
    min_shared: int,
    pair_cap: int,
) -> list[PairRow]:
    # Plain subqueries let SQLite join on the link tables' own comic_id indexes. Without
    # ANALYZE statistics it will not build a temporary index for a materialized CTE and
    # compares every row on one side with every row on the other.
    link_a = links_a.subquery("link_a")
    link_b = links_b.subquery("link_b")

    join_on = link_a.c.comic_id == link_b.c.comic_id
    if same_side:
        join_on = and_(join_on, link_a.c.entity_id < link_b.c.entity_id)

    # Each link row is one comic per entity, so a plain count is the shared issue count.
    shared_issues = func.count()
    pairs = (
        select(
            link_a.c.entity_id.label("a_id"),
            link_b.c.entity_id.label("b_id"),
            shared_issues.label("shared_issues"),
        )
        .select_from(link_a.join(link_b, join_on))
        .group_by(link_a.c.entity_id, link_b.c.entity_id)
        .having(shared_issues >= min_shared)
        .subquery("pairs")
    )

    name_a = aliased(entity_a)
    name_b = aliased(entity_b)
    rows = db.execute(
        select(pairs.c.a_id, name_a.name, pairs.c.b_id, name_b.name, pairs.c.shared_issues)
        .select_from(pairs)
        .join(name_a, name_a.id == pairs.c.a_id)
        .join(name_b, name_b.id == pairs.c.b_id)
        .order_by(pairs.c.shared_issues.desc(), name_a.name.asc(), name_b.name.asc())
        .limit(pair_cap)
    )
    return [PairRow(*row) for row in rows]


def _series_stats(
    db: Session,
    pairs: list[PairRow],
    links_a: Select,
    links_b: Select,
) -> dict[tuple[int, int], tuple]:
    """Shared series count and a sample series name. Only run for the pairs being shown."""
    link_a = _restrict_links(links_a, sorted({pair.person_a_id for pair in pairs})).subquery("link_a")
    link_b = _restrict_links(links_b, sorted({pair.person_b_id for pair in pairs})).subquery("link_b")
    rows = db.execute(
        select(
            link_a.c.entity_id,
            link_b.c.entity_id,
            func.count(func.distinct(Volume.series_id)),
            func.min(Series.name),
        )
        .select_from(link_a)
        .join(link_b, link_b.c.comic_id == link_a.c.comic_id)
        .join(Comic, Comic.id == link_a.c.comic_id)
        .join(Volume, Comic.volume_id == Volume.id)
        .join(Series, Volume.series_id == Series.id)
        .group_by(link_a.c.entity_id, link_b.c.entity_id)
    )
    return {(row[0], row[1]): (row[2], row[3]) for row in rows}


def _collaboration_pairs(
    db: Session,
    links_a: Select,
    links_b: Select,
    entity_a,
    entity_b,
    same_side: bool,
    min_shared: int,
    pair_cap: int,
) -> list[PairRow]:
    """Top pairs by shared issues, then by names, capped at pair_cap."""
    freq_a = _entity_frequencies(db, links_a)
    freq_b = freq_a if same_side else _entity_frequencies(db, links_b)
    if not freq_a or not freq_b:
        return []

    candidates = PAIR_CANDIDATES_START
    while True:
        top_a = top_b = None
        left_out_max = 0
        if candidates <= PAIR_CANDIDATES_MAX:
            if len(freq_a) > candidates:
                top_a = [entity_id for entity_id, _ in freq_a[:candidates]]
                left_out_max = freq_a[candidates][1]
            if same_side:
                top_b = top_a
            elif len(freq_b) > candidates:
                top_b = [entity_id for entity_id, _ in freq_b[:candidates]]
                left_out_max = max(left_out_max, freq_b[candidates][1])

        pairs = _count_top_pairs(
            db,
            _restrict_links(links_a, top_a),
            _restrict_links(links_b, top_b),
            entity_a,
            entity_b,
            same_side,
            min_shared,
            pair_cap,
        )
        weakest_kept = pairs[-1].shared_issues if len(pairs) == pair_cap else min_shared
        if left_out_max < weakest_kept:
            return pairs
        candidates *= PAIR_CANDIDATES_GROWTH


def _collaboration_payload(
    db: Session,
    links_a: Select,
    links_b: Select,
    entity_a,
    entity_b,
    same_side: bool,
    min_shared: int,
    limit: int,
    params: dict,
) -> dict:
    pairs = _collaboration_pairs(
        db,
        links_a,
        links_b,
        entity_a,
        entity_b,
        same_side,
        min_shared,
        pair_cap=max(limit * limit * 2, 50),
    )
    if not pairs:
        return _empty_matrix_payload(**params)

    def load_series_stats(shown_pairs: list[PairRow]) -> dict[tuple[int, int], tuple]:
        return _series_stats(db, shown_pairs, links_a, links_b)

    return {**params, **_serialize_visible_matrix(pairs, limit, load_series_stats)}


@router.get("/creator-collaborations", name="creator_collaborations")
def get_creator_collaborations(
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

    def build() -> dict:
        visible_comic_ids = _visible_comic_ids(current_user, library_id)
        return _collaboration_payload(
            db,
            _credit_links(role_a, visible_comic_ids),
            _credit_links(role_b, visible_comic_ids),
            Person,
            Person,
            same_side=role_a == role_b,
            min_shared=min_shared,
            limit=limit,
            params={
                "role_a": role_a,
                "role_b": role_b,
                "library_id": library_id,
                "min_shared": min_shared,
                "limit": limit,
            },
        )

    key = ("creator", role_a, role_b, library_id, min_shared, limit)
    return _cached_payload(db, current_user, key, build)


@router.get("/character-collaborations", name="character_collaborations")
def get_character_collaborations(
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

    def build() -> dict:
        links = _character_links(_visible_comic_ids(current_user, library_id))
        return _collaboration_payload(
            db,
            links,
            links,
            Character,
            Character,
            same_side=True,
            min_shared=min_shared,
            limit=limit,
            params={
                "library_id": library_id,
                "min_shared": min_shared,
                "limit": limit,
            },
        )

    key = ("character", library_id, min_shared, limit)
    return _cached_payload(db, current_user, key, build)


@router.get("/creator-character-collaborations", name="creator_character_collaborations")
def get_creator_character_collaborations(
    db: SessionDep,
    current_user: CurrentUser,
    role_a: CreatorRole = Query("writer"),
    library_id: Optional[int] = Query(None, ge=1),
    min_shared: int = Query(2, ge=1, le=100),
    limit: int = Query(12, ge=2, le=15, description="Maximum creators and characters shown per axis"),
):
    """
    Aggregates creator-to-character pairings for heatmap-style insights.
    """
    if library_id is not None:
        _ensure_library_access(library_id, current_user)

    def build() -> dict:
        visible_comic_ids = _visible_comic_ids(current_user, library_id)
        return _collaboration_payload(
            db,
            _credit_links(role_a, visible_comic_ids),
            _character_links(visible_comic_ids),
            Person,
            Character,
            same_side=False,
            min_shared=min_shared,
            limit=limit,
            params={
                "role_a": role_a,
                "library_id": library_id,
                "min_shared": min_shared,
                "limit": limit,
            },
        )

    key = ("creator_character", role_a, library_id, min_shared, limit)
    return _cached_payload(db, current_user, key, build)
