from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, select
from typing import List

from app.database import get_db
from app.models.comic import Comic
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
        query: str = Query(..., min_length=1),
        db: Session = Depends(get_db)
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