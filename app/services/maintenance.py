from pathlib import Path
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
import logging
import os

from app.config import settings
from app.core.settings_loader import get_system_setting
from app.core.path_utils import resolve_absolute_path
from app.models.tags import Character, Team, Location
from app.models.credits import Person
from app.models.comic import Comic, Volume
from app.models.job import JobStatus, ScanJob
from app.models.library_root import LibraryRoot
from app.models.series import Series
from app.models.reading_list import ReadingList
from app.models.collection import Collection

from app.services.enrichment import EnrichmentResult, EnrichmentService
from app.services.images import ImageService


class MaintenanceService:
    def __init__(self, db: Session):
        self.db = db
        self.logger = logging.getLogger(__name__)
        self.enrichment = EnrichmentService()

    def _active_root_paths_for_cleanup(self, library_id: int = None) -> dict[int, str]:
        query = self.db.query(LibraryRoot).filter(LibraryRoot.is_active == True)
        if library_id:
            query = query.filter(LibraryRoot.library_id == library_id)

        roots = query.order_by(LibraryRoot.id).all()
        missing_roots = [root for root in roots if not os.path.exists(root.path)]
        if missing_roots:
            missing_root = missing_roots[0]
            raise FileNotFoundError(
                "Janitor cleanup aborted because active library root path "
                f"does not exist: {missing_root.path}"
            )

        return {root.id: root.path for root in roots}

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
            # Scoped to "comicinfo" only -- never "manual" (a user may deliberately
            # keep an empty one) and never "cbl" (CBL list lifecycle is owned by
            # CBLSourceService.rebuild()/delete(); this cleanup job never rebuilds
            # CBL sources afterward, so deleting an empty CBL list here would just
            # orphan its CBLSource until the next full scan).
            stats["empty_lists"] = self.db.query(ReadingList).filter(~ReadingList.items.any()).filter(
                ReadingList.source == "comicinfo").delete(synchronize_session=False)
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
        root_paths = self._active_root_paths_for_cleanup(library_id)

        query = self.db.query(Comic)
        if library_id:
            query = query.join(Volume).join(Series).filter(Series.library_id == library_id)

        comics = query.all()
        deleted_ids = []

        for comic in comics:
            if comic.library_root_id not in root_paths:
                root = self.db.get(LibraryRoot, comic.library_root_id)
                if root is not None and not root.is_active:
                    self.logger.info(
                        "Janitor: Skipping inactive library root for %s (%s)",
                        comic.filename,
                        root.path,
                    )
                    continue

                root_paths[comic.library_root_id] = root.path if root else None

            root_path = root_paths[comic.library_root_id]
            path_to_check = resolve_absolute_path(root_path, comic.relative_path) if root_path else None

            if not path_to_check or not os.path.exists(path_to_check):
                self.logger.info(f"Janitor: Removing missing file: {comic.filename} ({path_to_check})")
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

    def cleanup_old_jobs(self) -> int:
        """
        Delete terminal job history rows older than the configured retention window.
        """
        retention_days = max(1, get_system_setting("jobs.retention_days", 30))

        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

        deleted = (
            self.db.query(ScanJob)
            .filter(ScanJob.status.in_([JobStatus.COMPLETED, JobStatus.FAILED]))
            .filter(
                or_(
                    ScanJob.completed_at < cutoff,
                    and_(ScanJob.completed_at.is_(None), ScanJob.created_at < cutoff),
                )
            )
            .delete(synchronize_session=False)
        )

        if deleted:
            self.db.commit()
            self.logger.info(f"Janitor: Deleted {deleted} old job history row(s).")

        return deleted

    def refresh_reading_list_descriptions(self, *, allow_online: bool = False) -> dict:
        """Populate missing descriptions for ComicInfo-derived lists.

        Scoped to source == "comicinfo" only -- a CBL-derived list's description
        comes from the .cbl file itself and must not be clobbered by this lookup.
        """
        lists = self.db.query(ReadingList).filter(ReadingList.source == "comicinfo").all()
        stats = {
            "updated": 0,
            "unchanged": 0,
            "not_found": 0,
            "local_hits": 0,
            "online_hits": 0,
            "total_scanned": len(lists),
        }

        for r_list in lists:
            result = self.enrichment.lookup_description(
                r_list.name,
                allow_online=allow_online,
            )
            if not isinstance(result, EnrichmentResult):
                # Backwards-compatible guard for tests or local integrations that
                # replace the enrichment service with a simple description lookup.
                result = EnrichmentResult(description=result)

            description = result.description
            if not description:
                stats["not_found"] += 1
                continue

            if result.source == "local":
                stats["local_hits"] += 1
            elif result.source == "wikipedia":
                stats["online_hits"] += 1

            if description and description != r_list.description:
                r_list.description = description
                stats["updated"] += 1

                # Batch commit every 50 to avoid holding lock too long
                if stats["updated"] % 50 == 0:
                    self.db.commit()
            else:
                stats["unchanged"] += 1

        if stats["updated"] > 0:
            self.db.commit()

        return stats
