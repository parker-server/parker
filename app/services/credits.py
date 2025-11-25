from sqlalchemy.orm import Session
from typing import Dict, List
from app.models.credits import Person, ComicCredit
from app.models.comic import Comic


class CreditService:
    """Service for managing comic credits"""

    # Map ComicInfo.xml field names to role names
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

    def get_or_create_person(self, name: str) -> Person:
        """Get existing person or create new one"""
        name = name.strip()
        person = self.db.query(Person).filter(Person.name == name).first()

        if not person:
            person = Person(name=name)
            self.db.add(person)
            self.db.commit()
            self.db.refresh(person)

        return person

    def parse_credit_field(self, field_value: str) -> List[str]:
        """Parse comma-separated names from a credit field and deduplicate"""
        if not field_value:
            return []

        # Split by comma and clean up
        names = [n.strip() for n in field_value.split(',') if n.strip()]

        # Deduplicate while preserving order
        unique_names = list(dict.fromkeys(names))

        return unique_names

    def add_credits_to_comic(self, comic: Comic, metadata: Dict):
        """Add all credits from metadata to a comic"""
        # Clear existing credits first
        self.db.query(ComicCredit).filter(ComicCredit.comic_id == comic.id).delete()

        # Process each credit field
        for metadata_field, role in self.ROLE_MAPPING.items():
            field_value = metadata.get(metadata_field)
            if field_value:
                names = self.parse_credit_field(field_value)
                for name in names:
                    person = self.get_or_create_person(name)

                    # Create credit record
                    credit = ComicCredit(
                        comic_id=comic.id,
                        person_id=person.id,
                        role=role
                    )
                    self.db.add(credit)

        self.db.commit()

    def get_all_people_by_role(self, role: str) -> List[Person]:
        """Get all people who have worked in a specific role"""
        return self.db.query(Person).join(ComicCredit).filter(
            ComicCredit.role == role
        ).distinct().all()

    def get_person_comics(self, person_id: int, role: str = None) -> List[Comic]:
        """Get all comics a person worked on, optionally filtered by role"""
        query = self.db.query(Comic).join(ComicCredit).filter(
            ComicCredit.person_id == person_id
        )

        if role:
            query = query.filter(ComicCredit.role == role)

        return query.all()