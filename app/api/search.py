from fastapi import APIRouter, Depends, Query
from typing import List, Annotated, Optional
from sqlalchemy.orm import Query as SqlQuery

from app.api.deps import SessionDep, CurrentUser
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.library import Library
from app.models.tags import Character, Team, Location
from app.models.credits import Person, ComicCredit
from app.models.collection import Collection, CollectionItem
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.pull_list import PullList

router = APIRouter()

def _get_allowed_library_ids(user) -> Optional[List[int]]:
    """Returns list of allowed IDs, or None if superuser (all allowed)"""
    if user.is_superuser:
        return None
    return [lib.id for lib in user.accessible_libraries]


def _apply_library_filter(query: SqlQuery, model, allowed_ids: List[int]) -> SqlQuery:
    """
    Dynamically joins to Series to filter by Library ID.
    Assumes the query is starting from 'model'.
    """
    if allowed_ids is None:
        return query

    # 1. Series (Direct)
    if model == Series:
        return query.filter(Series.library_id.in_(allowed_ids))

    # 2. Library (Direct)
    if model == Library:
        return query.filter(Library.id.in_(allowed_ids))

    # 3. Comic (Direct) -> Volume -> Series
    if model == Comic:
        return query.join(Volume).join(Series).filter(Series.library_id.in_(allowed_ids))

    # 4. Tags/People (Many-to-Many via Comic)
    # We join the relationship to Comic, then up to Series
    # Note: This filters to "Entities appearing in at least one visible book"
    if model in [Character, Team, Location]:
        return query.join(model.comics).join(Volume).join(Series).filter(Series.library_id.in_(allowed_ids))

    if model == Person:
        # Person -> ComicCredit -> Comic -> Volume -> Series
        return query.join(ComicCredit).join(Comic).join(Volume).join(Series).filter(Series.library_id.in_(allowed_ids))

    # 5. Containers (Collection/ReadingList)
    if model == Collection:
        return query.join(CollectionItem).join(Comic).join(Volume).join(Series).filter(
            Series.library_id.in_(allowed_ids))

    # Reading List Logic
    # Only show lists where the user can see at least one book.
    if model == ReadingList:
        return query.join(ReadingListItem).join(Comic).join(Volume).join(Series).filter(
            Series.library_id.in_(allowed_ids))


    return query


@router.get("/suggestions")
async def get_search_suggestions(
        field: str,
        db: SessionDep,
        current_user: CurrentUser,
        query: Annotated[str, Query(min_length=1)] = ...,
):
    """
    Autocomplete suggestions for search filters.
    Scoped to User's Accessible Libraries.
    e.g. ?field=character&query=Bat -> ["Batman", "Batgirl"]
    """
    q_str = query.lower()
    results = []
    allowed_ids = _get_allowed_library_ids(current_user)

    # Helper to build base query
    def build_query(model, column):
        base = db.query(column).filter(column.ilike(f"%{q_str}%"))
        return _apply_library_filter(base, model, allowed_ids)

    # Map fields to their models/columns
    if field == 'series':
        results = build_query(Series, Series.name).limit(10).all()

    elif field == 'library':
        results = build_query(Library, Library.name).limit(10).all()

    elif field == 'publisher':
        # Publisher is a column on Comic, so we distinct it
        results = build_query(Comic, Comic.publisher).distinct().limit(10).all()

    elif field == 'character':
        results = build_query(Character, Character.name).limit(10).all()

    elif field == 'team':
        results = build_query(Team, Team.name).limit(10).all()

    elif field in ['writer', 'penciller', 'inker', 'colorist', 'letterer', 'editor', 'cover_artist']:
        # For people, we check the Person table, but ideally we'd filter by role if we had that link easily available
        # For speed, just searching Person names is usually fine
        results = build_query(Person, Person.name).limit(10).all()

    elif field == 'collection':
        results = build_query(Collection, Collection.name).limit(10).all()

    elif field == 'location':
        results = build_query(Location, Location.name).limit(10).all()

    elif field == 'format':
        # Distinct query on Comic table
        results = build_query(Comic, Comic.format).distinct().limit(10).all()

    elif field == 'imprint':
        # Distinct query on Comic table
        results = build_query(Comic, Comic.imprint).distinct().limit(10).all()

    elif field == 'age_rating':
        # Suggest distinct Age Ratings present in the library
        results = build_query(Comic, Comic.age_rating).distinct().limit(10).all()

    elif field == 'language':
        # Suggest distinct Language Codes (e.g., 'en', 'jp')
        results = build_query(Comic, Comic.language_iso).distinct().limit(10).all()

    elif field == 'reading_list':
        results = build_query(ReadingList, ReadingList.name).limit(10).all()

    elif field == 'pull_list':
        results = (db.query(PullList.name)
                   .filter(PullList.name.ilike(f"%{q_str}%"), PullList.user_id == current_user.id)
                   .limit(10).all())

    # Flatten list of tuples [('Name',), ...] -> ['Name', ...]
    return [r[0] for r in results if r[0]]


@router.get("/quick")
async def quick_search(
        db: SessionDep,
        current_user: CurrentUser,
        q: str = Query(..., min_length=2)
):
    """
    Multi-model segmented search for Navbar autocomplete.
    Searches Series, Collections, Lists, People, and Tags.
    Scoped to User's Accessible Libraries.
    """
    limit = 5  # Tighter limit per category
    q_str = f"%{q}%"
    allowed_ids = _get_allowed_library_ids(current_user)

    results = {}

    # Helper for quick search queries
    def get_scoped_results(model, name_col):
        base = db.query(model).filter(name_col.ilike(q_str))
        return _apply_library_filter(base, model, allowed_ids).limit(limit).all()

    # 1. Series (Scoped to User)
    series_objs = get_scoped_results(Series, Series.name)
    results["series"] = [{"id": s.id, "name": s.name, "year": s.created_at.year} for s in series_objs]

    # 2. Collections
    collections_objs = get_scoped_results(Collection, Collection.name)
    results["collections"] = [{"id": c.id, "name": c.name} for c in collections_objs]

    # 3. Reading Lists (Global for now, or scope if strict RLS needed)
    lists_objs = db.query(ReadingList).filter(ReadingList.name.ilike(q_str)).limit(limit).all()
    results["reading_lists"] = [{"id": l.id, "name": l.name} for l in lists_objs]

    # 4. People (Creators)
    people_objs = get_scoped_results(Person, Person.name)
    results["people"] = [{"id": p.id, "name": p.name} for p in people_objs]

    # 5. Tags
    chars_objs = get_scoped_results(Character, Character.name)
    results["characters"] = [{"id": c.id, "name": c.name} for c in chars_objs]

    teams_objs = get_scoped_results(Team, Team.name)
    results["teams"] = [{"id": t.id, "name": t.name} for t in teams_objs]

    locs_objs = get_scoped_results(Location, Location.name)
    results["locations"] = [{"id": l.id, "name": l.name} for l in locs_objs]

    # 6. Pull Lists (User scoped)
    pull_list_objs =(db.query(PullList)
                     .filter(PullList.name.ilike(q_str), PullList.user_id == current_user.id)
                     .limit(limit).all())
    results['pull_lists'] = [{"id": p.id, "name": p.name} for p in pull_list_objs]

    return results