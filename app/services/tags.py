from sqlalchemy.orm import Session
from typing import List, Dict
from app.models import Character, Team, Location

class TagService:
    """Service for managing tags with Caching and Deferred Commits"""

    def __init__(self, db: Session):
        self.db = db
        # Cache to store objects by name to avoid DB lookups
        self.character_cache: Dict[str, Character] = {}
        self.team_cache: Dict[str, Team] = {}
        self.location_cache: Dict[str, Location] = {}

    def get_or_create_character(self, name: str) -> Character:
        name = name.strip()
        if not name:
            return None

        # 1. Check Cache
        if name in self.character_cache:
            return self.character_cache[name]

        # 2. Check DB
        character = self.db.query(Character).filter(Character.name == name).first()

        if not character:
            # 3. Create (Flush only)
            character = Character(name=name)
            self.db.add(character)
            self.db.flush()  # Generate ID without disk write

        # 4. Update Cache
        self.character_cache[name] = character
        return character

    def get_or_create_characters(self, names: str) -> List[Character]:
        if not names:
            return []
        name_list = [n.strip() for n in names.split(',') if n.strip()]
        # Deduplicate
        unique_names = list(dict.fromkeys(name_list))
        return [self.get_or_create_character(n) for n in unique_names if n]

    def get_or_create_team(self, name: str) -> Team:
        name = name.strip()
        if not name:
            return None

        if name in self.team_cache:
            return self.team_cache[name]

        team = self.db.query(Team).filter(Team.name == name).first()
        if not team:
            team = Team(name=name)
            self.db.add(team)
            self.db.flush()

        self.team_cache[name] = team
        return team

    def get_or_create_teams(self, names: str) -> List[Team]:
        if not names:
            return []
        name_list = [n.strip() for n in names.split(',') if n.strip()]
        unique_names = list(dict.fromkeys(name_list))
        return [self.get_or_create_team(n) for n in unique_names if n]

    def get_or_create_location(self, name: str) -> Location:
        name = name.strip()
        if not name:
            return None

        if name in self.location_cache:
            return self.location_cache[name]

        location = self.db.query(Location).filter(Location.name == name).first()
        if not location:
            location = Location(name=name)
            self.db.add(location)
            self.db.flush()

        self.location_cache[name] = location
        return location

    def get_or_create_locations(self, names: str) -> List[Location]:
        if not names:
            return []
        name_list = [n.strip() for n in names.split(',') if n.strip()]
        unique_names = list(dict.fromkeys(name_list))
        return [self.get_or_create_location(n) for n in unique_names if n]