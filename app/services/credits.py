from sqlalchemy.orm import Session
from typing import Dict, List
from app.models import Person, ComicCredit, Comic

class CreditService:
    """Service for managing comic credits with Caching"""

    ROLE_MAPPING = {
        'writer': 'writer',
        'penciller': 'penciller',
        'inker': 'inker',
        'colorist': 'colorist',
        'letterer': 'letterer',
        'cover_artist': 'cover_artist',
        'editor': 'editor',
    }

    def __init__(self, db: Session):
        self.db = db
        self.person_cache: Dict[str, Person] = {}

    def get_or_create_person(self, name: str) -> Person:
        name = name.strip()
        if not name:
            return None

        if name in self.person_cache:
            return self.person_cache[name]

        person = self.db.query(Person).filter(Person.name == name).first()

        if not person:
            person = Person(name=name)
            self.db.add(person)
            self.db.flush()  # ID needed for credit relationship

        self.person_cache[name] = person
        return person

    def parse_credit_field(self, field_value: str) -> List[str]:
        if not field_value:
            return []
        names = [n.strip() for n in field_value.split(',') if n.strip()]
        return list(dict.fromkeys(names))

    def add_credits_to_comic(self, comic: Comic, metadata: Dict):
        """Add all credits from metadata to a comic"""
        # Clear existing credits
        # This is a bulk delete, usually fast enough without optimization
        self.db.query(ComicCredit).filter(ComicCredit.comic_id == comic.id).delete()

        for metadata_field, role in self.ROLE_MAPPING.items():
            field_value = metadata.get(metadata_field)
            if field_value:
                names = self.parse_credit_field(field_value)
                for name in names:
                    person = self.get_or_create_person(name)
                    if person:
                        credit = ComicCredit(
                            comic_id=comic.id,
                            person_id=person.id,
                            role=role
                        )
                        self.db.add(credit)
                        # No flush needed here, the objects just sit in session until batch commit

        # REMOVED self.db.commit() - Scanner handles this