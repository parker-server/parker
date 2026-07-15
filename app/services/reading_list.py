import logging
from sqlalchemy.orm import Session, joinedload
from typing import Optional, Dict
from app.models import ReadingList, ReadingListItem, Comic, Volume, Series
from app.services.enrichment import EnrichmentService

class ReadingListService:
    def __init__(self, db: Session):
        self.db = db
        self.list_cache: Dict[str, ReadingList] = {}
        self.logger = logging.getLogger(__name__)
        self.enrichment = EnrichmentService()

    def get_or_create_reading_list(self, name: str) -> ReadingList:
        name = name.strip()

        if name in self.list_cache:
            return self.list_cache[name]

        reading_list = self.db.query(ReadingList).filter(ReadingList.name == name).first()

        if not reading_list:
            reading_list = ReadingList(name=name, auto_generated=1)

            # Attempt to enrich description
            # Since this is async, we ideally await it.
            # If this method is sync, we might need to run it synchronously or background it.
            # For simplicity, let's assume we can run it:
            description = self.enrichment.get_description(name)
            if description:
                reading_list.description = description

            self.db.add(reading_list)
            self.db.flush()
            print(f"Created reading list: {name}")
            self.logger.debug(f"Created reading list: {name}")

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

    def remove_library_comics_from_all_lists(self, library_id: int) -> int:
        comic_ids_query = (
            self.db.query(Comic.id)
            .join(Volume, Comic.volume_id == Volume.id)
            .join(Series, Volume.series_id == Series.id)
            .filter(Series.library_id == library_id)
        )
        return self.db.query(ReadingListItem).filter(
            ReadingListItem.comic_id.in_(comic_ids_query)
        ).delete(synchronize_session=False)

    def _get_list_items_for_comic(self, comic_id: int) -> list[ReadingListItem]:
        return (
            self.db.query(ReadingListItem)
            .options(joinedload(ReadingListItem.reading_list))
            .filter(ReadingListItem.comic_id == comic_id)
            .all()
        )

    def update_comic_reading_lists(self, comic: Comic, alternate_series: Optional[str],
                                   alternate_number: Optional[str]):
        target_name = alternate_series.strip() if alternate_series and alternate_series.strip() else None
        target_position = None

        if target_name and alternate_number:
            try:
                target_position = float(alternate_number)
            except (TypeError, ValueError):
                target_name = None
        else:
            target_name = None

        current_items = self._get_list_items_for_comic(comic.id)

        if not current_items:
            if target_name and target_position is not None:
                self.add_comic_to_list(comic, target_name, target_position)
            return

        if (
            target_name
            and target_position is not None
            and len(current_items) == 1
            and current_items[0].reading_list
            and current_items[0].reading_list.name == target_name
        ):
            if current_items[0].position != target_position:
                current_items[0].position = target_position
            return

        for item in current_items:
            self.db.delete(item)

        if target_name and target_position is not None:
            self.add_comic_to_list(comic, target_name, target_position)

    def cleanup_empty_lists(self):
        # This usually runs at the end of the scan, safe to run logic here
        # but let the scanner commit it.
        empty_lists = self.db.query(ReadingList).filter(~ReadingList.items.any()).all()
        for rl in empty_lists:
            self.db.delete(rl)
            # Invalidate cache if we delete
            if rl.name in self.list_cache:
                del self.list_cache[rl.name]
