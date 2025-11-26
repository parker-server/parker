from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, func
from typing import List, Dict, Any
from app.models import (Comic, Volume, Series,
                        Character, Team, Location,
                        Person, ComicCredit,
                        Collection, CollectionItem,
                        ReadingList, ReadingListItem)

from app.schemas.search import SearchRequest, SearchFilter


class SearchService:
    """Service for searching comics with complex filters"""

    def __init__(self, db: Session):
        self.db = db

    def search(self, request: SearchRequest) -> Dict[str, Any]:
        """Execute search based on request parameters"""
        # Start with base query
        query = self.db.query(Comic).join(Volume).join(Series)

        # Build filter conditions
        if request.filters:
            conditions = [self._build_condition(f) for f in request.filters]

            # Combine conditions with AND or OR
            if request.match == 'all':
                for condition in conditions:
                    if condition is not None:
                        query = query.filter(condition)
            else:  # any
                valid_conditions = [c for c in conditions if c is not None]
                if valid_conditions:
                    query = query.filter(or_(*valid_conditions))

        # Get total count before pagination
        total = query.count()

        # Apply sorting
        query = self._apply_sorting(query, request.sort_by, request.sort_order)

        # Apply pagination
        query = query.limit(request.limit).offset(request.offset)

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

        # Handle "is_empty" and "is_not_empty" operators (no value needed)
        if operator == 'is_empty':
            return self._build_empty_condition(field, True)
        elif operator == 'is_not_empty':
            return self._build_empty_condition(field, False)

        # For other operators, value is required
        if value is None:
            return None

        # Build condition based on field type
        if field in ['series', 'volume', 'number', 'title', 'publisher', 'imprint', 'format', 'year', 'series_group']:
            return self._build_simple_field_condition(field, operator, value)
        elif field in ['writer', 'penciller', 'inker', 'colorist', 'letterer', 'cover_artist', 'editor']:
            return self._build_credit_condition(field, operator, value)
        elif field == 'character':
            return self._build_tag_condition(Character, operator, value)
        elif field == 'team':
            return self._build_tag_condition(Team, operator, value)
        elif field == 'location':
            return self._build_tag_condition(Location, operator, value)
        elif field == 'collection':
            return self._build_collection_condition(operator, value)
        elif field == 'reading_list':
            return self._build_reading_list_condition(operator, value)

        return None

    def _build_simple_field_condition(self, field: str, operator: str, value):
        """Build condition for simple comic fields"""
        # Map field names to Comic model attributes
        field_map = {
            'series': Series.name,
            'title': Comic.title,
            'number': Comic.number,
            'publisher': Comic.publisher,
            'imprint': Comic.imprint,
            'format': Comic.format,
            'year': Comic.year,
            'series_group': Comic.series_group
        }

        column = field_map.get(field)
        if column is None:
            return None

        if operator == 'equal':
            return column == value
        elif operator == 'not_equal':
            return column != value
        elif operator == 'contains':
            if isinstance(value, list):
                # For multiple values with contains, match any
                return or_(*[column.ilike(f"%{v}%") for v in value])
            return column.ilike(f"%{value}%")
        elif operator == 'does_not_contain':
            if isinstance(value, list):
                # For multiple values, must not contain any
                return and_(*[~column.ilike(f"%{v}%") for v in value])
            return ~column.ilike(f"%{value}%")
        elif operator == 'must_contain':
            if isinstance(value, list):
                # For multiple values, must contain all
                return and_(*[column.ilike(f"%{v}%") for v in value])
            return column.ilike(f"%{value}%")

        return None

    def _build_credit_condition(self, role: str, operator: str, value):
        """Build condition for credits (writer, artist, etc.)"""
        # Subquery to find comics with specific credits
        if operator == 'equal':
            return Comic.credits.any(
                and_(
                    ComicCredit.role == role,
                    ComicCredit.person.has(Person.name == value)
                )
            )
        elif operator == 'not_equal':
            return ~Comic.credits.any(
                and_(
                    ComicCredit.role == role,
                    ComicCredit.person.has(Person.name == value)
                )
            )
        elif operator == 'contains':
            if isinstance(value, list):
                # Match any of the values
                return Comic.credits.any(
                    and_(
                        ComicCredit.role == role,
                        or_(*[ComicCredit.person.has(Person.name.ilike(f"%{v}%")) for v in value])
                    )
                )
            return Comic.credits.any(
                and_(
                    ComicCredit.role == role,
                    ComicCredit.person.has(Person.name.ilike(f"%{value}%"))
                )
            )
        elif operator == 'does_not_contain':
            if isinstance(value, list):
                return ~Comic.credits.any(
                    and_(
                        ComicCredit.role == role,
                        or_(*[ComicCredit.person.has(Person.name.ilike(f"%{v}%")) for v in value])
                    )
                )
            return ~Comic.credits.any(
                and_(
                    ComicCredit.role == role,
                    ComicCredit.person.has(Person.name.ilike(f"%{value}%"))
                )
            )
        elif operator == 'must_contain':
            # This is complex - need all values
            if isinstance(value, list):
                conditions = []
                for v in value:
                    conditions.append(
                        Comic.credits.any(
                            and_(
                                ComicCredit.role == role,
                                ComicCredit.person.has(Person.name.ilike(f"%{v}%"))
                            )
                        )
                    )
                return and_(*conditions)
            return Comic.credits.any(
                and_(
                    ComicCredit.role == role,
                    ComicCredit.person.has(Person.name.ilike(f"%{value}%"))
                )
            )

        return None

    def _build_tag_condition(self, model_class, operator: str, value):
        """Build condition for tags (characters, teams, locations)"""
        if operator == 'equal':
            if model_class == Character:
                return Comic.characters.any(Character.name == value)
            elif model_class == Team:
                return Comic.teams.any(Team.name == value)
            elif model_class == Location:
                return Comic.locations.any(Location.name == value)
        elif operator == 'not_equal':
            if model_class == Character:
                return ~Comic.characters.any(Character.name == value)
            elif model_class == Team:
                return ~Comic.teams.any(Team.name == value)
            elif model_class == Location:
                return ~Comic.locations.any(Location.name == value)
        elif operator == 'contains':
            if isinstance(value, list):
                if model_class == Character:
                    return Comic.characters.any(Character.name.in_(value))
                elif model_class == Team:
                    return Comic.teams.any(Team.name.in_(value))
                elif model_class == Location:
                    return Comic.locations.any(Location.name.in_(value))
            else:
                if model_class == Character:
                    return Comic.characters.any(Character.name.ilike(f"%{value}%"))
                elif model_class == Team:
                    return Comic.teams.any(Team.name.ilike(f"%{value}%"))
                elif model_class == Location:
                    return Comic.locations.any(Location.name.ilike(f"%{value}%"))
        elif operator == 'must_contain':
            # Must have ALL specified tags
            if isinstance(value, list):
                conditions = []
                for v in value:
                    if model_class == Character:
                        conditions.append(Comic.characters.any(Character.name == v))
                    elif model_class == Team:
                        conditions.append(Comic.teams.any(Team.name == v))
                    elif model_class == Location:
                        conditions.append(Comic.locations.any(Location.name == v))
                return and_(*conditions)

        return None

    def _build_collection_condition(self, operator: str, value):
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

    def _build_reading_list_condition(self, operator: str, value):
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

    def _build_empty_condition(self, field: str, is_empty: bool):
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

        # For simple fields
        column = field_map.get(field)
        if column is not None:
            if is_empty:
                return or_(column.is_(None), column == '')
            else:
                return and_(column.isnot(None), column != '')

        return None

    def _apply_sorting(self, query, sort_by: str, sort_order: str):
        """Apply sorting to query"""
        if sort_by == 'created':
            order_col = Comic.created_at
        elif sort_by == 'year':
            order_col = Comic.year
        elif sort_by == 'series':
            order_col = Series.name
        elif sort_by == 'title':
            order_col = Comic.title
        else:
            order_col = Comic.created_at

        if sort_order == 'desc':
            query = query.order_by(order_col.desc())
        else:
            query = query.order_by(order_col.asc())

        return query

    def _format_comic(self, comic: Comic) -> dict:
        """Format comic for response"""
        return {
            "id": comic.id,
            "series": comic.volume.series.name,
            "volume": comic.volume.volume_number,
            "number": comic.number,
            "title": comic.title,
            "year": comic.year,
            "publisher": comic.publisher,
            "filename": comic.filename
        }