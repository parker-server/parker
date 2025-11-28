from fastapi import APIRouter, Depends, Query
from typing import List, Annotated

from app.api.deps import SessionDep, CurrentUser
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.library import Library
from app.models.tags import Character, Team, Location
from app.models.credits import Person, ComicCredit
from app.models.collection import Collection
from app.models.reading_list import ReadingList

router = APIRouter()


@router.get("/suggestions")
async def get_search_suggestions(
        field: str,
        db: SessionDep,
        current_user: CurrentUser,
        query: Annotated[str, Query(min_length=1)] = ...,
):
    """
    Autocomplete suggestions for search filters.
    e.g. ?field=character&query=Bat -> ["Batman", "Batgirl"]
    """
    query = query.lower()
    results = []

    # Map fields to their models/columns
    if field == 'series':
        results = db.query(Series.name).filter(Series.name.ilike(f"%{query}%")).limit(10).all()

    elif field == 'library':
        results = db.query(Library.name).filter(Library.name.ilike(f"%{query}%")).limit(10).all()

    elif field == 'publisher':
        # Publisher is a column on Comic, so we distinct it
        results = db.query(Comic.publisher).filter(Comic.publisher.ilike(f"%{query}%")).distinct().limit(10).all()

    elif field == 'character':
        results = db.query(Character.name).filter(Character.name.ilike(f"%{query}%")).limit(10).all()

    elif field == 'team':
        results = db.query(Team.name).filter(Team.name.ilike(f"%{query}%")).limit(10).all()

    elif field in ['writer', 'penciller', 'inker', 'colorist', 'letterer', 'editor', 'cover_artist']:
        # For people, we check the Person table, but ideally we'd filter by role if we had that link easily available
        # For speed, just searching Person names is usually fine
        results = db.query(Person.name).filter(Person.name.ilike(f"%{query}%")).limit(10).all()

    elif field == 'collection':
        results = db.query(Collection.name).filter(Collection.name.ilike(f"%{query}%")).limit(10).all()

    elif field == 'location':
        results = db.query(Location.name).filter(Location.name.ilike(f"%{query}%")).limit(10).all()

    elif field == 'format':
        # Distinct query on Comic table
        results = db.query(Comic.format).filter(Comic.format.ilike(f"%{query}%")).distinct().limit(10).all()

    elif field == 'imprint':
        # Distinct query on Comic table
        results = db.query(Comic.imprint).filter(Comic.imprint.ilike(f"%{query}%")).distinct().limit(10).all()

    elif field == 'reading_list':
        results = db.query(ReadingList.name).filter(ReadingList.name.ilike(f"%{query}%")).limit(10).all()

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
    """
    limit = 5  # Tighter limit per category
    q_str = f"%{q}%"

    results = {}

    # 1. Series (Scoped to User)
    series_query = db.query(Series)
    if not current_user.is_superuser:
        allowed_ids = [lib.id for lib in current_user.accessible_libraries]
        series_query = series_query.filter(Series.library_id.in_(allowed_ids))

    series_objs = series_query.filter(Series.name.ilike(q_str)).limit(limit).all()
    results["series"] = [{"id": s.id, "name": s.name, "year": s.created_at.year} for s in series_objs]

    # 2. Collections
    collections_objs = db.query(Collection).filter(Collection.name.ilike(q_str)).limit(limit).all()
    results["collections"] = [{"id": c.id, "name": c.name} for c in collections_objs]

    # 3. Reading Lists
    lists_objs = db.query(ReadingList).filter(ReadingList.name.ilike(q_str)).limit(limit).all()
    results["reading_lists"] = [{"id": l.id, "name": l.name} for l in lists_objs]

    # 4. People (Creators)
    people_objs = db.query(Person).filter(Person.name.ilike(q_str)).limit(limit).all()
    results["people"] = [{"id": p.id, "name": p.name} for p in people_objs]

    # 5. Tags
    chars_objs = db.query(Character).filter(Character.name.ilike(q_str)).limit(limit).all()
    results["characters"] = [{"id": c.id, "name": c.name} for c in chars_objs]

    teams_objs = db.query(Team).filter(Team.name.ilike(q_str)).limit(limit).all()
    results["teams"] = [{"id": t.id, "name": t.name} for t in teams_objs]

    locs_objs = db.query(Location).filter(Location.name.ilike(q_str)).limit(limit).all()
    results["locations"] = [{"id": l.id, "name": l.name} for l in locs_objs]

    return results