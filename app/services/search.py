from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, func, not_
from typing import List, Dict, Any, Union
from app.models import (Comic, Volume, Series,
                        Character, Team, Location, Genre,
                        Person, ComicCredit,
                        Collection, CollectionItem,
                        ReadingList, ReadingListItem,
                        PullList, PullListItem,
                        Library, User)

from app.schemas.search import SearchRequest, SearchFilter


class SearchService:
    """Service for searching comics with complex filters"""

    def __init__(self, db: Session, current_user: User):
        self.db = db
        self.user = current_user

    def search(self, request: SearchRequest) -> Dict[str, Any]:
        """Execute search based on request parameters"""
        # Start with base query joining essential tables
        query = self.db.query(Comic).join(Volume).join(Series)

        # 1. NEW: Apply Library Context Scope
        if hasattr(request, 'context_library_id') and request.context_library_id:
            query = query.filter(Series.library_id == request.context_library_id)

        # Build filter conditions
        if request.filters:
            conditions = []
            for f in request.filters:
                # Skip invalid filters
                cond = self._build_condition(f)
                if cond is not None:
                    conditions.append(cond)

            # Combine conditions with AND or OR
            if not conditions:
                pass  # No valid filters, return all (scoped by library)
            elif request.match == 'all':
                query = query.filter(and_(*conditions))
            else:  # 'any'
                query = query.filter(or_(*conditions))

        # Get total count before pagination
        # Optimization: Count distinct IDs to handle joins correctly
        total = query.distinct(Comic.id).count()

        # Apply sorting
        query = self._apply_sorting(query, request.sort_by, request.sort_order)

        # Apply pagination
        query = query.offset(request.offset).limit(request.limit)

        # Execute and format results
        comics = query.all()
        results = [self._format_comic(comic) for comic in comics]

        return {
            "total": total,
            "limit": request.limit,
            "offset": request.offset,
            "results": results
        }

    def _build_condition(self, filter: SearchFilter):
        """Build a SQLAlchemy condition from a filter"""
        field = filter.field
        operator = filter.operator
        value = filter.value

        # Handle "is_empty" / "is_not_empty" (No value needed)
        if operator == 'is_empty':
            return self._build_empty_condition(field, True)
        elif operator == 'is_not_empty':
            return self._build_empty_condition(field, False)

        # For other operators, value is required
        if value is None:
            return None

        # --- ROUTING LOGIC ---

        # 1. Simple Fields (Direct columns on Comic or Series)
        if field == 'series':
            return self._build_simple_field_condition(Series.name, operator, value)
        elif field == 'library':
            # Join is already implicit via Series -> Library, or we add explicit join if needed
            # But usually filtering by Series.library.name works if joined.
            # For safety, let's assume we might need to join Library if not already.
            # Ideally, ensure query joins Library if filtering by it.
            # Simplification: We filter on Library Name
            return self._build_simple_field_condition(Library.name, operator, value, needs_join=Library)

        elif field in ['title', 'number', 'publisher', 'imprint', 'format', 'year', 'series_group']:
            # Map string field name to Column object
            col_map = {
                'title': Comic.title,
                'number': Comic.number,
                'publisher': Comic.publisher,
                'imprint': Comic.imprint,
                'format': Comic.format,
                'year': Comic.year,
                'series_group': Comic.series_group
            }
            return self._build_simple_field_condition(col_map[field], operator, value)

        # 2. Credits (Writer, Penciller, etc.)
        elif field in ['writer', 'penciller', 'inker', 'colorist', 'letterer', 'cover_artist', 'editor']:
            return self._build_credit_condition(field, operator, value)

        # 3. Tags (Many-to-Many)
        elif field == 'character':
            return self._build_tag_condition(Comic.characters, Character.name, operator, value)
        elif field == 'team':
            return self._build_tag_condition(Comic.teams, Team.name, operator, value)
        elif field == 'location':
            return self._build_tag_condition(Comic.locations, Location.name, operator, value)
        elif field == 'genre':
            return self._build_tag_condition(Comic.genres, Genre.name, operator, value)

        # 4. Collections / Reading Lists / Pull Lists
        elif field == 'collection':
            return self._build_collection_condition(operator, value)
        elif field == 'reading_list':
            return self._build_reading_list_condition(operator, value)
        elif field == 'pull_list':
            return self._build_pull_list_condition(operator, value)

        return None

    @staticmethod
    def _build_simple_field_condition(column, operator, value, needs_join=None):
        """Build condition for simple fields"""
        # Note: 'needs_join' is a hint for complex queries, but SQLAlchemy often auto-resolves
        # if the relationship path is clear.

        # Ensure value is list for list-based operators
        values = value if isinstance(value, list) else [value]
        single_val = values[0] if values else None

        if operator == 'equal':
            return column == single_val
        elif operator == 'not_equal':
            return column != single_val

        elif operator == 'contains':
            # Case-insensitive partial match
            return column.ilike(f"%{single_val}%")
        elif operator == 'does_not_contain':
            return ~column.ilike(f"%{single_val}%")

        # Logic for multiple values in simple fields (e.g. Publisher IN [DC, Marvel])
        # Your 'must_contain' logic for simple fields is interesting (AND), but usually simple fields
        # can only be one thing (Publisher can't be DC AND Marvel).
        # We'll treat 'must_contain' as 'IN' for simple fields if logical.

        return None

    @staticmethod
    def _build_credit_condition(role: str, operator: str, value):
        """Build condition for credits using subqueries"""
        values = value if isinstance(value, list) else [value]

        # Helper to build the inner "Person Name" check
        def person_check(val):
            return ComicCredit.person.has(Person.name.ilike(f"%{val}%"))

        if operator == 'equal':  # Exact match on name
            return Comic.credits.any(and_(ComicCredit.role == role, ComicCredit.person.has(Person.name == values[0])))

        elif operator == 'contains':  # OR Logic: Has "Moore" OR "Morrison"
            checks = [person_check(v) for v in values]
            return Comic.credits.any(and_(ComicCredit.role == role, or_(*checks)))

        elif operator == 'does_not_contain':
            # NOT (Has "Moore" OR Has "Morrison")
            checks = [person_check(v) for v in values]
            return ~Comic.credits.any(and_(ComicCredit.role == role, or_(*checks)))

        elif operator == 'must_contain':  # AND Logic: Has "Moore" AND Has "Gibbons"
            # Requires multiple EXISTS clauses
            conditions = []
            for v in values:
                conditions.append(Comic.credits.any(and_(ComicCredit.role == role, person_check(v))))
            return and_(*conditions)

        return None

    @staticmethod
    def _build_tag_condition(relationship, name_column, operator, value):
        """Generic builder for Many-to-Many tags (Characters, Teams, Locations)"""
        values = value if isinstance(value, list) else [value]

        if operator == 'equal':
            return relationship.any(name_column == values[0])

        elif operator == 'contains':  # OR
            return relationship.any(name_column.in_(values))  # Exact match from list
            # OR if you want partial match:
            # checks = [name_column.ilike(f"%{v}%") for v in values]
            # return relationship.any(or_(*checks))

        elif operator == 'does_not_contain':
            return ~relationship.any(name_column.in_(values))

        elif operator == 'must_contain':  # AND
            conditions = [relationship.any(name_column == v) for v in values]
            return and_(*conditions)

        return None

    @staticmethod
    def _build_collection_condition(operator: str, value):
        """Build condition for collections"""
        if operator == 'equal':
            return Comic.collection_items.any(
                CollectionItem.collection.has(Collection.name == value)
            )
        elif operator == 'contains':
            if isinstance(value, list):
                return Comic.collection_items.any(
                    CollectionItem.collection.has(Collection.name.in_(value))
                )
            return Comic.collection_items.any(
                CollectionItem.collection.has(Collection.name.ilike(f"%{value}%"))
            )

        return None

    @staticmethod
    def _build_reading_list_condition(operator: str, value):
        """Build condition for reading lists"""
        if operator == 'equal':
            return Comic.reading_list_items.any(
                ReadingListItem.reading_list.has(ReadingList.name == value)
            )
        elif operator == 'contains':
            if isinstance(value, list):
                return Comic.reading_list_items.any(
                    ReadingListItem.reading_list.has(ReadingList.name.in_(value))
                )
            return Comic.reading_list_items.any(
                ReadingListItem.reading_list.has(ReadingList.name.ilike(f"%{value}%"))
            )

        return None

    @staticmethod
    def _build_empty_condition(field: str, is_empty: bool):
        """Build condition for checking if field is empty/null"""

        field_map = {
            'title': Comic.title,
            'publisher': Comic.publisher,
            'imprint': Comic.imprint,
            'format': Comic.format,
            'series_group': Comic.series_group,
        }

        # For relationship fields
        if field == 'character':
            return ~Comic.characters.any() if is_empty else Comic.characters.any()
        elif field == 'team':
            return ~Comic.teams.any() if is_empty else Comic.teams.any()
        elif field == 'location':
            return ~Comic.locations.any() if is_empty else Comic.locations.any()
        elif field == 'collection':
            return ~Comic.collection_items.any() if is_empty else Comic.collection_items.any()
        elif field == 'reading_list':
            return ~Comic.reading_list_items.any() if is_empty else Comic.reading_list_items.any()
        elif field in ['writer', 'penciller', 'inker', 'colorist', 'letterer', 'cover_artist', 'editor']:
            if is_empty:
                return ~Comic.credits.any(ComicCredit.role == field)
            else:
                return Comic.credits.any(ComicCredit.role == field)
        elif field == 'pull_list':
            return ~Comic.pull_list_items.any() if is_empty else Comic.pull_list_items.any()

        # For simple fields
        column = field_map.get(field)
        if column is not None:
            if is_empty:
                return or_(column.is_(None), column == '')
            else:
                return and_(column.isnot(None), column != '')

        return None


    def _build_pull_list_condition(self, operator: str, value):
        """Build condition for pull lists"""

        # Helper for common logic: Name match AND User match
        def scope_check(name_condition):
            return and_(PullList.user_id == self.user.id, name_condition)

        if operator == 'equal':
            return Comic.pull_list_items.any(
                PullListItem.pull_list.has(scope_check(PullList.name == value))
            )
        elif operator == 'contains':
            if isinstance(value, list):
                # OR logic for list of names
                return Comic.pull_list_items.any(
                    PullListItem.pull_list.has(scope_check(PullList.name.in_(value)))
                )
            # Partial match
            return Comic.pull_list_items.any(
                PullListItem.pull_list.has(scope_check(PullList.name.ilike(f"%{value}%")))
            )

        return None

    @staticmethod
    def _apply_sorting(query, sort_by: str, sort_order: str):
        if sort_by == 'series':
            col = Series.name
        elif sort_by == 'year':
            col = Comic.year
        elif sort_by == 'title':
            col = Comic.title
        elif sort_by == 'page_count':  # Added this one from schema
            col = Comic.page_count
        elif sort_by == 'updated':
            col = Comic.updated_at
        elif sort_by == 'created':  # (Explicit)
            col = Comic.created_at
        else:
            col = Comic.created_at # Default fallback

        if sort_order == 'desc':
            return query.order_by(col.desc())
        return query.order_by(col.asc())

    @staticmethod
    def _format_comic(comic: Comic) -> dict:
        """Format comic for response grid"""

        return {
            "id": comic.id,
            "series": comic.volume.series.name,
            "volume": comic.volume.volume_number,
            "number": comic.number,
            "title": comic.title,
            "year": comic.year,
            "publisher": comic.publisher,
            "format": comic.format,
            "thumbnail_path": f"/api/comics/{comic.id}/thumbnail"
        }