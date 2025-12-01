import logging
from sqlalchemy.orm import Session
from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.series import Series
from app.services.images import ImageService
from pathlib import Path


class ThumbnailService:
    def __init__(self, db: Session, library_id: int = None):
        self.db = db
        self.library_id = library_id
        self.image_service = ImageService()
        self.logger = logging.getLogger(__name__)

    def process_missing_thumbnails(self, force: bool = False):
        """
        Find comics in this library without thumbnails and generate them.
        """

        # GUARD: Ensure we actually have a library ID before running a library-wide scan
        if not self.library_id:
            raise ValueError("Library ID required for library-wide processing")

        query = self.db.query(Comic).join(Comic.volume).join(Series).filter(Series.library_id == self.library_id)

        if not force:
            # Only get ones missing paths
            query = query.filter(Comic.thumbnail_path == None)

        comics = query.all()

        stats = {"processed": 0, "errors": 0, "skipped": 0}

        for comic in comics:
            # Double check file existence if path exists (unless forcing)
            if not force and comic.thumbnail_path and Path(comic.thumbnail_path).exists():
                stats["skipped"] += 1
                continue

            try:
                # Define path
                target_path = Path(f"./storage/cover/comic_{comic.id}.webp")

                # Generate
                success = self.image_service.generate_thumbnail(comic.file_path, target_path)

                if success:
                    comic.thumbnail_path = str(target_path)
                    # Commit every item or batch (e.g. every 10)
                    self.db.commit()
                    stats["processed"] += 1
                else:
                    stats["errors"] += 1

            except Exception as e:
                print(f"Thumbnail error {comic.id}: {e}")
                stats["errors"] += 1

        return stats

    def process_series_thumbnails(self, series_id: int):
        """
        Force regenerate thumbnails for ALL comics in a series.
        """
        # Get all comics for this series
        comics = self.db.query(Comic).join(Volume).filter(Volume.series_id == series_id).all()
        return self._generate_batch(comics)

    def _generate_batch(self, comics: list) -> dict:
        """Helper to process a list of comics"""
        stats = {"processed": 0, "errors": 0, "skipped": 0}

        for comic in comics:
            try:
                target_path = Path(f"./storage/cover/comic_{comic.id}.webp")

                # Always generate (Overwrites existing)
                success = self.image_service.generate_thumbnail(comic.file_path, target_path)

                if success:
                    comic.thumbnail_path = str(target_path)
                    self.db.commit()
                    stats["processed"] += 1
                else:
                    stats["errors"] += 1

            except Exception as e:
                print(f"Thumbnail error {comic.id}: {e}")
                self.logger.error(f"Thumbnail error {comic.id}: {e}")
                stats["errors"] += 1

        return stats


