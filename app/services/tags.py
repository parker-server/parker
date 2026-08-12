from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import List, Dict, TypeVar
from app.models import Character, Team, Location, Genre

TagModel = TypeVar("TagModel", Character, Team, Location, Genre)


class TagService:
    """Service for managing tags with Caching and Deferred Commits"""

    def __init__(self, db: Session):
        self.db = db
        # Cache to store objects by normalized name to avoid DB lookups
        self.character_cache: Dict[str, Character] = {}
        self.team_cache: Dict[str, Team] = {}
        self.location_cache: Dict[str, Location] = {}
        self.genre_cache: Dict[str, Genre] = {}

    def _normalize_key(self, name: str) -> str:
        return name.casefold()

    def _parse_tag_names(self, names: str) -> List[str]:
        if not names:
            return []

        unique_names = []
        seen_keys = set()
        for raw_name in names.split(','):
            name = raw_name.strip()
            if not name:
                continue

            key = self._normalize_key(name)
            if key in seen_keys:
                continue

            seen_keys.add(key)
            unique_names.append(name)

        return unique_names

    def _get_or_create_tag(
        self,
        model: type[TagModel],
        cache: Dict[str, TagModel],
        name: str,
    ) -> TagModel | None:
        name = name.strip()
        if not name:
            return None

        key = self._normalize_key(name)
        if key in cache:
            return cache[key]

        tag = (
            self.db.query(model)
            .filter(func.lower(model.name) == key)
            .order_by(model.id)
            .first()
        )
        if not tag:
            tag = model(name=name)
            self.db.add(tag)
            self.db.flush()  # Generate ID without disk write

        cache[key] = tag
        return tag

    def get_or_create_character(self, name: str) -> Character | None:
        return self._get_or_create_tag(Character, self.character_cache, name)

    def get_or_create_characters(self, names: str) -> List[Character]:
        unique_names = self._parse_tag_names(names)
        return [
            character
            for n in unique_names
            if (character := self.get_or_create_character(n)) is not None
        ]

    def get_or_create_team(self, name: str) -> Team | None:
        return self._get_or_create_tag(Team, self.team_cache, name)

    def get_or_create_teams(self, names: str) -> List[Team]:
        unique_names = self._parse_tag_names(names)
        return [
            team
            for n in unique_names
            if (team := self.get_or_create_team(n)) is not None
        ]

    def get_or_create_location(self, name: str) -> Location | None:
        return self._get_or_create_tag(Location, self.location_cache, name)

    def get_or_create_locations(self, names: str) -> List[Location]:
        unique_names = self._parse_tag_names(names)
        return [
            location
            for n in unique_names
            if (location := self.get_or_create_location(n)) is not None
        ]

    def get_or_create_genre(self, name: str) -> Genre | None:
        return self._get_or_create_tag(Genre, self.genre_cache, name)

    def get_or_create_genres(self, names: str) -> List[Genre]:
        unique_names = self._parse_tag_names(names)
        return [
            genre
            for n in unique_names
            if (genre := self.get_or_create_genre(n)) is not None
        ]

