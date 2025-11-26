from sqlalchemy.orm import Session
from typing import Optional, Dict
from app.models import ReadingList, ReadingListItem, Comic


class ReadingListService:
    def __init__(self, db: Session):
        self.db = db
        self.list_cache: Dict[str, ReadingList] = {}

    def get_or_create_reading_list(self, name: str) -> ReadingList:
        name = name.strip()

        if name in self.list_cache:
            return self.list_cache[name]

        reading_list = self.db.query(ReadingList).filter(ReadingList.name == name).first()

        if not reading_list:
            reading_list = ReadingList(name=name, auto_generated=1)
            self.db.add(reading_list)
            self.db.flush()
            print(f"Created reading list: {name}")

        self.list_cache[name] = reading_list
        return reading_list

    def add_comic_to_list(self, comic: Comic, list_name: str, position: float):
        reading_list = self.get_or_create_reading_list(list_name)

        existing = self.db.query(ReadingListItem).filter(
            ReadingListItem.reading_list_id == reading_list.id,
            ReadingListItem.comic_id == comic.id
        ).first()

        if existing:
            if existing.position != position:
                existing.position = position
                # No commit
        else:
            item = ReadingListItem(
                reading_list_id=reading_list.id,
                comic_id=comic.id,
                position=position
            )
            self.db.add(item)
            # No commit

    def remove_comic_from_all_lists(self, comic_id: int):
        self.db.query(ReadingListItem).filter(
            ReadingListItem.comic_id == comic_id
        ).delete()
        # No commit

    def update_comic_reading_lists(self, comic: Comic, alternate_series: Optional[str],
                                   alternate_number: Optional[str]):
        self.remove_comic_from_all_lists(comic.id)

        if alternate_series and alternate_number:
            try:
                position = float(alternate_number)
                self.add_comic_to_list(comic, alternate_series, position)
            except ValueError:
                pass

    def cleanup_empty_lists(self):
        # This usually runs at the end of the scan, safe to run logic here
        # but let the scanner commit it.
        empty_lists = self.db.query(ReadingList).filter(~ReadingList.items.any()).all()
        for rl in empty_lists:
            self.db.delete(rl)
            # Invalidate cache if we delete
            if rl.name in self.list_cache:
                del self.list_cache[rl.name]