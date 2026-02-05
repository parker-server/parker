from pathlib import Path
from sqlalchemy.orm import Session
import logging
import os

from app.config import settings
from app.models.tags import Character, Team, Location
from app.models.credits import Person
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.reading_list import ReadingList
from app.models.collection import Collection

from app.services.enrichment import EnrichmentService
from app.services.images import ImageService


class MaintenanceService:
    def __init__(self, db: Session):
        self.db = db
        self.logger = logging.getLogger(__name__)
        self.enrichment = EnrichmentService()

    def cleanup_orphans(self, library_id: int = None) -> dict:
        """
        Delete metadata entities that are no longer associated with any comics.
        OPTIMIZED: Commits after each step to yield the DB write lock.
        OPTIMIZED: Only runs heavy 'Global Tag' cleanup if library_id is None.
        """
        stats = {
            "series": 0,
            "volumes": 0,
            "characters": 0,
            "teams": 0,
            "locations": 0,
            "people": 0,
            "empty_lists": 0,
            "empty_collections": 0
        }

        # 1. Clean Empty Volumes (No comics linked)
        # This is fast and scoped to the library if provided
        # We use synchronize_session=False for speed since we are in a batch operation
        vol_query = self.db.query(Volume).filter(~Volume.comics.any())
        if library_id:
            # Join Series to check library_id
            # We cannot use .join() with .delete(). We must use a subquery.
            # Subquery: Find all Series IDs belonging to this library
            series_subquery = self.db.query(Series.id).filter(Series.library_id == library_id)
            vol_query = vol_query.filter(Volume.series_id.in_(series_subquery))

        stats["volumes"] = vol_query.delete(synchronize_session=False)
        self.db.commit()  # Yield Lock

        # 2. Clean Empty Series
        series_query = self.db.query(Series).filter(~Series.volumes.any())
        if library_id:
            series_query = series_query.filter(Series.library_id == library_id)

        stats["series"] = series_query.delete(synchronize_session=False)
        self.db.commit()  # Yield Lock

        # --- HEAVY OPERATIONS BELOW ---
        # We ONLY run these if this is a Global Cleanup (library_id is None).
        # It is inefficient to check global tags after every single library scan.

        if library_id is None:

            self.logger.info("Performing deep global cleanup (Tags, People, Collections)...")

            # 3. Clean Tags (Characters)
            stats["characters"] = self.db.query(Character).filter(~Character.comics.any()).delete(synchronize_session=False)
            self.db.commit()  # Yield Lock

            # 4. Clean Teams
            stats["teams"] = self.db.query(Team).filter(~Team.comics.any()).delete(synchronize_session=False)
            self.db.commit()  # Yield Lock

            # 5. Clean Locations
            stats["locations"] = self.db.query(Location).filter(~Location.comics.any()).delete(synchronize_session=False)
            self.db.commit()  # Yield Lock

            # 6. Clean People
            stats["people"] = self.db.query(Person).filter(~Person.credits.any()).delete(synchronize_session=False)
            self.db.commit()  # Yield Lock

            # 7. Clean Empty Containers
            stats["empty_lists"] = self.db.query(ReadingList).filter(~ReadingList.items.any()).filter(
                ReadingList.auto_generated == True).delete(synchronize_session=False)
            self.db.commit()  # Yield Lock

            stats["empty_collections"] = self.db.query(Collection).filter(~Collection.items.any()).filter(
                Collection.auto_generated == True).delete(synchronize_session=False)
            self.db.commit()  # Yield Lock

        else:
            self.logger.info(f"Skipping deep tag cleanup for scoped scan (Library {library_id})")

        return stats

    def cleanup_missing_files(self, library_id: int = None) -> list[int]:
        """
        Removes dead records and returns a list of their IDs for thumbnail cleanup.
        """
        query = self.db.query(Comic)
        if library_id:
            query = query.join(Volume).join(Series).filter(Series.library_id == library_id)

        comics = query.all()
        deleted_ids = []

        for comic in comics:
            if not os.path.exists(comic.file_path):
                self.logger.info(f"Janitor: Removing missing file: {comic.file_path}")
                deleted_ids.append(comic.id)
                self.db.delete(comic)

                if len(deleted_ids) % 100 == 0:
                    self.db.commit()

        if deleted_ids:
            self.db.commit()

        return deleted_ids

    def delete_thumbnails_by_id(self, comic_ids: list[int]):
        """
        Targeted deletion based on your 'cover_{id}.webp' naming convention.
        """
        for c_id in comic_ids:
            # Construct the path based on your scoped naming convention
            # Using .as_posix() to ensure we handle the slashes correctly for Linux/Docker
            thumb_path = (settings.cover_dir / f"cover_{c_id}.webp")

            if thumb_path.exists():
                try:
                    thumb_path.unlink()
                    self.logger.debug(f"Janitor: Deleted thumbnail for removed comic {c_id}")
                except Exception as e:
                    self.logger.error(f"Failed to delete thumb {thumb_path}: {e}")

    def cleanup_orphaned_thumbnails(self) -> int:
        """
        Delete thumbnail files from storage that are no longer linked to any Comic.
        Uses POSIX normalization to bridge Windows dev and Linux production environments.
        """
        self.logger.info("Janitor: Starting orphaned thumbnail cleanup...")

        # 1. Get all valid image paths currently in the Comic table
        comic_thumbs = self.db.query(Comic.thumbnail_path).filter(Comic.thumbnail_path != None).all()

        # .as_posix() converts all backslashes to forward slashes for a unified set
        valid_thumbnails = {Path(t[0]).as_posix() for t in comic_thumbs}

        # 2. Walk the thumbnail directory
        thumb_root = settings.cover_dir
        deleted_count = 0

        if not thumb_root.exists():
            return 0

        for thumb_file in thumb_root.rglob('*'):
            if thumb_file.is_file():

                # We normalize the physical file to a POSIX-style relative path
                # This matches the 'storage/cover/comic.webp' format stored in the DB
                normalized_disk_path = thumb_file.as_posix()

                # Check if this physical file is in our 'Valid' set from the DB
                if normalized_disk_path not in valid_thumbnails:
                    try:
                        self.logger.info(f"Janitor: Deleting unreferenced thumbnail: {normalized_disk_path}")
                        thumb_file.unlink()
                        deleted_count += 1
                    except Exception as e:
                        self.logger.error(f"Failed to delete orphaned thumb {thumb_file}: {e}")

        self.logger.info(f"Janitor: Deleted {deleted_count} orphaned thumbnail files.")
        return deleted_count

    def refresh_reading_list_descriptions(self) -> dict:
        """Populate missing descriptions for auto-generated lists."""
        lists = self.db.query(ReadingList).filter(ReadingList.auto_generated == True).all()
        updated_count = 0

        for r_list in lists:
            description = self.enrichment.get_description(r_list.name)
            if description and description != r_list.description:
                r_list.description = description
                updated_count += 1

                # Batch commit every 50 to avoid holding lock too long
                if updated_count % 50 == 0:
                    self.db.commit()

        if updated_count > 0:
            self.db.commit()

        return {"updated": updated_count, "total_scanned": len(lists)}
    