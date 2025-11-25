from sqlalchemy.orm import Session
from typing import Optional
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.comic import Comic


class ReadingListService:
    """Service for managing reading lists"""

    def __init__(self, db: Session):
        self.db = db

    def get_or_create_reading_list(self, name: str) -> ReadingList:
        """Get existing reading list or create new one"""
        name = name.strip()
        reading_list = self.db.query(ReadingList).filter(ReadingList.name == name).first()

        if not reading_list:
            reading_list = ReadingList(name=name, auto_generated=1)
            self.db.add(reading_list)
            self.db.commit()
            self.db.refresh(reading_list)
            print(f"Created reading list: {name}")

        return reading_list

    def add_comic_to_list(self, comic: Comic, list_name: str, position: float):
        """Add a comic to a reading list at a specific position"""
        reading_list = self.get_or_create_reading_list(list_name)

        # Check if comic already in this list
        existing = self.db.query(ReadingListItem).filter(
            ReadingListItem.reading_list_id == reading_list.id,
            ReadingListItem.comic_id == comic.id
        ).first()

        if existing:
            # Update position if changed
            if existing.position != position:
                print(f"Updating position for {comic.filename} in '{list_name}': {existing.position} -> {position}")
                existing.position = position
                self.db.commit()
        else:
            # Create new item
            item = ReadingListItem(
                reading_list_id=reading_list.id,
                comic_id=comic.id,
                position=position
            )
            self.db.add(item)
            self.db.commit()
            print(f"Added {comic.filename} to reading list '{list_name}' at position {position}")

    def remove_comic_from_all_lists(self, comic_id: int):
        """Remove a comic from all reading lists"""
        self.db.query(ReadingListItem).filter(
            ReadingListItem.comic_id == comic_id
        ).delete()
        self.db.commit()

    def update_comic_reading_lists(self, comic: Comic, alternate_series: Optional[str],
                                   alternate_number: Optional[str]):
        """Update a comic's reading list membership based on AlternateSeries tags"""
        # First, remove from all auto-generated lists
        self.remove_comic_from_all_lists(comic.id)

        # If comic has AlternateSeries and AlternateNumber, add to that list
        if alternate_series and alternate_number:
            try:
                # Parse position - handle decimals like "1.5" or whole numbers
                position = float(alternate_number)
                self.add_comic_to_list(comic, alternate_series, position)
            except ValueError:
                print(f"Warning: Invalid AlternateNumber '{alternate_number}' for {comic.filename}")

    def cleanup_empty_lists(self):
        """Remove reading lists that have no items"""
        empty_lists = self.db.query(ReadingList).filter(
            ~ReadingList.items.any()
        ).all()

        for reading_list in empty_lists:
            print(f"Removing empty reading list: {reading_list.name}")
            self.db.delete(reading_list)

        if empty_lists:
            self.db.commit()