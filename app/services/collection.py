import logging
from sqlalchemy.orm import Session
from typing import Optional, Dict

from app.models import Collection, CollectionItem, Comic, Volume, Series

class CollectionService:
    def __init__(self, db: Session):
        self.db = db
        self.collection_cache: Dict[str, Collection] = {}
        self.logger = logging.getLogger(__name__)

    def get_or_create_collection(self, name: str) -> Collection:
        name = name.strip()

        if name in self.collection_cache:
            return self.collection_cache[name]

        collection = self.db.query(Collection).filter(Collection.name == name).first()

        if not collection:
            collection = Collection(name=name, auto_generated=1)
            self.db.add(collection)
            self.db.flush()
            print(f"Created collection: {name}")
            self.logger.debug(f"Created collection: {name}")

        self.collection_cache[name] = collection
        return collection

    def add_comic_to_collection(self, comic: Comic, collection_name: str):
        collection = self.get_or_create_collection(collection_name)

        existing = self.db.query(CollectionItem).filter(
            CollectionItem.collection_id == collection.id,
            CollectionItem.comic_id == comic.id
        ).first()

        if not existing:
            item = CollectionItem(
                collection_id=collection.id,
                comic_id=comic.id
            )
            self.db.add(item)
            # No commit

    def remove_comic_from_all_collections(self, comic_id: int):
        self.db.query(CollectionItem).filter(
            CollectionItem.comic_id == comic_id
        ).delete()
        # No commit

    def remove_library_comics_from_all_collections(self, library_id: int) -> int:
        comic_ids_query = (
            self.db.query(Comic.id)
            .join(Volume, Comic.volume_id == Volume.id)
            .join(Series, Volume.series_id == Series.id)
            .filter(Series.library_id == library_id)
        )
        return self.db.query(CollectionItem).filter(
            CollectionItem.comic_id.in_(comic_ids_query)
        ).delete(synchronize_session=False)

    def update_comic_collections(self, comic: Comic, series_group: Optional[str]):
        self.remove_comic_from_all_collections(comic.id)
        if series_group:
            self.add_comic_to_collection(comic, series_group)

    def cleanup_empty_collections(self):
        empty_collections = self.db.query(Collection).filter(~Collection.items.any()).all()
        for col in empty_collections:
            self.db.delete(col)
            if col.name in self.collection_cache:
                del self.collection_cache[col.name]
