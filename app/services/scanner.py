from pathlib import Path
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from datetime import datetime
import json
import os
import time
import logging

from app.config import settings
from app.models.library import Library
from app.models.series import Series
from app.models.comic import Volume, Comic
from app.services.archive import ComicArchive
from app.services.metadata import parse_comicinfo
from app.services.tags import TagService
from app.services.credits import CreditService
from app.services.reading_list import ReadingListService
from app.services.collection import CollectionService
from app.services.images import ImageService


class LibraryScanner:
    """Scans library directories and imports comics with batch processing"""

    def __init__(self, library: Library, db: Session):
        self.library = library
        self.db = db
        self.supported_extensions = ['.cbz', '.cbr']
        self.tag_service = TagService(db)
        self.credit_service = CreditService(db)
        self.reading_list_service = ReadingListService(db)
        self.collection_service = CollectionService(db)
        self.image_service = ImageService()

        self.logger = logging.getLogger(__name__)

        # Local caches to reduce DB reads during the scan loop
        self.series_cache: Dict[str, Series] = {}
        self.volume_cache: Dict[str, Volume] = {}

    def scan(self, force: bool = False) -> dict:
        """
        Scan the library path and import comics using batch commits.
        """
        library_path = Path(self.library.path)

        if not library_path.exists():
            self.logger.error(f"Library path {self.library.path} does not exist")
            return {"error": f"Library path does not exist: {self.library.path}"}

        found_comics = []
        errors = []
        imported = 0
        updated = 0
        skipped = 0

        # Batch configuration
        BATCH_SIZE = 50
        pending_changes = 0

        print(f"Scanning {library_path}... (force={force})")
        self.logger.info(f"Scanning {library_path}... (force={force})")

        # Start timing
        start_time = time.time()

        # 1. OPTIMIZATION: Pre-fetch all existing comics for this library.
        # This avoids executing a SELECT query for every single file in the loop.
        print("Pre-fetching existing file list...")
        self.logger.info("Pre-fetching existing file list...")
        db_comics = self.db.query(Comic).join(Volume).join(Series).filter(
            Series.library_id == self.library.id
        ).all()

        # Map file_path -> Comic object for O(1) lookup
        existing_map = {c.file_path: c for c in db_comics}

        # Track paths found on disk to identify deletions later
        scanned_paths_on_disk = set()

        # Walk through directory
        for file_path in library_path.rglob('*'):
            if file_path.suffix.lower() in self.supported_extensions:
                file_path_str = str(file_path)
                scanned_paths_on_disk.add(file_path_str)

                try:
                    file_mtime = os.path.getmtime(file_path)
                    file_size_bytes = os.path.getsize(file_path)

                    # Check against our pre-fetched map
                    existing = existing_map.get(file_path_str)

                    comic = None
                    if existing:
                        # Check modification time
                        if not force and existing.file_modified_at and existing.file_modified_at >= file_mtime:
                            skipped += 1
                            continue
                        else:
                            # Update existing
                            if force:
                                print(f"Force scanning: {file_path.name}")
                                self.logger.info(f"Force scanning: {file_path.name}")
                            else:
                                print(f"Updating modified: {file_path.name}")
                                self.logger.info(f"Updating modified: {file_path.name}")

                            comic = self._update_comic(existing, file_path, file_mtime, file_size_bytes)

                            # TODO: if comic comes back as None, do something and dont increment

                            updated += 1
                            pending_changes += 1
                    else:
                        # Import new
                        comic = self._import_comic(file_path, file_mtime, file_size_bytes)
                        if comic:
                            imported += 1
                            pending_changes += 1
                            # Update our local map so duplicates in same run are caught (unlikely but safe)
                            existing_map[file_path_str] = comic

                    if comic:
                        found_comics.append({
                            "id": comic.id,
                            "filename": comic.filename,
                            # Use safe navigation in case relationship isn't refreshed yet
                            "series": comic.volume.series.name if comic.volume and comic.volume.series else "Unknown",
                            "pages": comic.page_count
                        })

                    # 2. OPTIMIZATION: Batch Commit
                    # Only hit the disk once every BATCH_SIZE items
                    if pending_changes >= BATCH_SIZE:
                        print(f"Committing batch of {pending_changes} items...")
                        self.logger.info(f"Committing batch of {pending_changes} items...")
                        self.db.commit()
                        pending_changes = 0

                except Exception as e:
                    # If an error occurs, we log it but try not to kill the whole scan
                    errors.append({"file": str(file_path), "error": str(e)})
                    print(f"Error processing {file_path}: {e}")
                    self.logger.error(f"Error processing {file_path}: {e}")
                    # In case of database error, we might need to rollback the current transaction
                    # to proceed, but that would lose the pending batch.
                    # Advanced logic would use savepoints, but simple try/catch per file is usually enough
                    # for metadata errors. DB errors will likely raise out.

        # Commit any remaining items
        if pending_changes > 0:
            print(f"Committing final batch of {pending_changes} items...")
            self.logger.info(f"Committing final batch of {pending_changes} items...")
            self.db.commit()

        # Find and remove comics whose files no longer exist
        # We pass the set we built during the loop
        deleted = self._cleanup_missing_files(scanned_paths_on_disk, existing_map)

        # Cleanup empty containers
        self.reading_list_service.cleanup_empty_lists()
        self.collection_service.cleanup_empty_collections()

        # Update library scan time
        self.library.last_scanned = datetime.utcnow()
        self.db.commit()

        elapsed_time = round(time.time() - start_time, 2)

        return {
            "library": self.library.name,
            "path": self.library.path,
            "force_scan": force,
            "found": len(found_comics),
            "imported": imported,
            "updated": updated,
            "deleted": deleted,
            "skipped": skipped,
            "errors": len(errors),
            "comics": found_comics[:10],
            "error_details": errors[:5],
            "elapsed": elapsed_time
        }

    def _cleanup_missing_files(self, scanned_paths_on_disk: set, existing_map: dict) -> int:
        """Remove comics from DB whose files no longer exist"""
        deleted = 0

        # Iterate over the map of comics we knew about at start
        for file_path, comic in existing_map.items():
            if file_path not in scanned_paths_on_disk:
                print(f"Removing deleted comic: {comic.filename}")
                self.logger.info(f"Removing deleted comic: {comic.filename}")
                self.db.delete(comic)
                deleted += 1

        if deleted > 0:
            self.db.commit()

        return deleted

    def _import_comic(self, file_path: Path, file_mtime: float, file_size_bytes: int) -> Optional[Comic]:
        """Process and import a new comic file"""
        metadata = self._extract_metadata(file_path)

        if not metadata:
            return None

        # Get or create series (Uses Cache)
        series_name = metadata.get('series', 'Unknown Series')
        series = self._get_or_create_series(series_name)

        # Get or create volume (Uses Cache)
        volume_num = int(metadata.get('volume', 1)) if metadata.get('volume') else 1
        volume = self._get_or_create_volume(series, volume_num)

        # Normalize number
        raw_number = metadata.get('number')
        clean_number = self._normalize_number(raw_number)

        # Create comic
        comic = Comic(
            volume_id=volume.id,
            filename=file_path.name,
            file_path=str(file_path),
            file_modified_at=file_mtime,
            file_size=file_size_bytes,
            page_count=metadata['page_count'],

            # Basic info
            number=clean_number,
            title=metadata.get('title'),
            summary=metadata.get('summary'),
            year=int(metadata.get('year')) if metadata.get('year') else None,
            month=int(metadata.get('month')) if metadata.get('month') else None,
            day=int(metadata.get('day')) if metadata.get('day') else None,
            web=metadata.get('web'),
            notes=metadata.get('notes'),
            age_rating=metadata.get('age_rating'),
            language_iso=metadata.get('lang'),

            # Publishing
            publisher=metadata.get('publisher'),
            imprint=metadata.get('imprint'),
            format=metadata.get('format'),
            series_group=metadata.get('series_group'),

            # Technical
            scan_information=metadata.get('scan_information'),

            # Reading lists
            alternate_series=metadata.get('alternate_series'),
            alternate_number=metadata.get('alternate_number'),
            story_arc=metadata.get('story_arc'),

            # Map the Count field
            count=int(metadata.get('count')) if metadata.get('count') else None,

            # Full metadata
            metadata_json=json.dumps(metadata.get('raw_metadata', {}))
        )

        self.db.add(comic)
        # CRITICAL: Use flush() instead of commit().
        # This assigns the PK (id) so we can use it for the thumbnail,
        # but keeps the transaction open for the batch.
        self.db.flush()

        # Add credits
        self.credit_service.add_credits_to_comic(comic, metadata)

        # Tags
        if metadata.get('characters'):
            comic.characters = self.tag_service.get_or_create_characters(metadata.get('characters'))

        if metadata.get('teams'):
            comic.teams = self.tag_service.get_or_create_teams(metadata.get('teams'))

        if metadata.get('locations'):
            comic.locations = self.tag_service.get_or_create_locations(metadata.get('locations'))

        if metadata.get('genre'):
            comic.genres = self.tag_service.get_or_create_genres(metadata.get('genre'))

        # Reading lists
        self.reading_list_service.update_comic_reading_lists(
            comic,
            metadata.get('alternate_series'),
            metadata.get('alternate_number')
        )

        # Collections
        self.collection_service.update_comic_collections(
            comic,
            metadata.get('series_group')
        )

        # Touch Parent Series to update 'updated_at'
        # This ensures it shows up in "Recently Updated"
        series.updated_at = datetime.utcnow()
        # Note: SQLAlchemy tracks dirty state, so this will trigger an UPDATE on commit


        print(f"Imported: {series_name} #{metadata.get('number', '?')} - {file_path.name}")
        self.logger.info(f"Imported: {series_name} #{metadata.get('number', '?')} - {file_path.name}")

        return comic

    def _update_comic(self, comic: Comic, file_path: Path, file_mtime: float, file_size_bytes: int) -> Optional[Comic]:
        """Update an existing comic with new metadata"""
        metadata = self._extract_metadata(file_path)

        if not metadata:
            return None

        # Check if series/volume changed
        series_name = metadata.get('series', 'Unknown Series')
        volume_num = int(metadata.get('volume', 1)) if metadata.get('volume') else 1

        series = self._get_or_create_series(series_name)
        volume = self._get_or_create_volume(series, volume_num)

        # Normalize number
        raw_number = metadata.get('number')
        clean_number = self._normalize_number(raw_number)

        # Update fields
        comic.volume_id = volume.id
        comic.file_modified_at = file_mtime
        comic.file_size = file_size_bytes
        comic.page_count = metadata['page_count']
        comic.number = clean_number
        comic.title = metadata.get('title')
        comic.summary = metadata.get('summary')
        comic.year = int(metadata.get('year')) if metadata.get('year') else None
        comic.month = int(metadata.get('month')) if metadata.get('month') else None
        comic.day = int(metadata.get('day')) if metadata.get('day') else None
        comic.web = metadata.get('web')
        comic.notes = metadata.get('notes')
        comic.age_rating = metadata.get('age_rating')
        comic.language_iso = metadata.get('lang')
        comic.publisher = metadata.get('publisher')
        comic.imprint = metadata.get('imprint')
        comic.format = metadata.get('format')
        comic.series_group = metadata.get('series_group')
        comic.scan_information = metadata.get('scan_information')
        comic.alternate_series = metadata.get('alternate_series')
        comic.alternate_number = metadata.get('alternate_number')
        comic.story_arc = metadata.get('story_arc')
        comic.count = int(metadata.get('count')) if metadata.get('count') else None
        comic.metadata_json = json.dumps(metadata.get('raw_metadata', {}))
        comic.updated_at = datetime.utcnow()

        # Update credits
        self.credit_service.add_credits_to_comic(comic, metadata)

        # Tags
        comic.characters.clear()
        comic.teams.clear()
        comic.locations.clear()

        if metadata.get('characters'):
            comic.characters = self.tag_service.get_or_create_characters(metadata.get('characters'))
        if metadata.get('teams'):
            comic.teams = self.tag_service.get_or_create_teams(metadata.get('teams'))
        if metadata.get('locations'):
            comic.locations = self.tag_service.get_or_create_locations(metadata.get('locations'))
        if metadata.get('genre'):
            comic.genres = self.tag_service.get_or_create_genres(metadata.get('genre'))

        self.reading_list_service.update_comic_reading_lists(
            comic,
            metadata.get('alternate_series'),
            metadata.get('alternate_number')
        )

        self.collection_service.update_comic_collections(
            comic,
            metadata.get('series_group')
        )

        # Touch Parent Series
        series.updated_at = datetime.utcnow()

        # NO COMMIT HERE - handled by batch loop

        return comic

    def _extract_metadata(self, file_path: Path) -> Optional[Dict]:
        """Extract metadata from comic archive"""
        try:
            with ComicArchive(file_path) as archive:
                pages = archive.get_pages()

                if not pages:
                    print(f"Warning: No valid image pages found in {file_path.name}")
                    self.logger.warning(f"Warning: No valid image pages found in {file_path.name}")
                    return None

                comicinfo_xml = archive.get_comicinfo()

                # 1. Establish Physical Truth of page count
                physical_count = len(pages)
                metadata = {'page_count': physical_count}

                if comicinfo_xml:
                    parsed = parse_comicinfo(comicinfo_xml)
                    metadata.update(parsed)

                    # Force overwrite: Always use physical count for this field.
                    # We trust the file system over the XML tag for navigational safety in the reader.
                    metadata['page_count'] = physical_count

                    metadata['raw_metadata'] = parsed

                return metadata

        except Exception as e:
            print(f"Error extracting metadata from {file_path}: {e}")
            self.logger.error(f"Error extracting metadata from {file_path}: {e}")
            return None

    def _get_or_create_series(self, name: str) -> Series:
        """Get existing series or create new one with Caching"""

        # 1. Check local cache
        if name in self.series_cache:
            return self.series_cache[name]

        # 2. Check Database
        series = self.db.query(Series).filter(
            Series.name == name,
            Series.library_id == self.library.id
        ).first()

        if not series:
            # 3. Create new (Flush, don't commit)
            series = Series(name=name, library_id=self.library.id)
            self.db.add(series)
            self.db.flush()

        # 4. Add to cache
        self.series_cache[name] = series
        return series

    def _get_or_create_volume(self, series: Series, volume_number: int) -> Volume:
        """Get existing volume or create new one with Caching"""

        # Composite key for cache
        cache_key = f"{series.id}_{volume_number}"

        if cache_key in self.volume_cache:
            return self.volume_cache[cache_key]

        volume = self.db.query(Volume).filter(
            Volume.series_id == series.id,
            Volume.volume_number == volume_number
        ).first()

        if not volume:
            volume = Volume(series_id=series.id, volume_number=volume_number)
            self.db.add(volume)
            self.db.flush()

        self.volume_cache[cache_key] = volume
        return volume

    def _generate_thumbnail(self, comic: Comic) -> None:
        try:
            storage_path = Path("./storage/cover")
            thumbnail_filename = f"comic_{comic.id}.webp"
            target_path = storage_path / thumbnail_filename

            # Use service to generate and save directly to target
            success = self.image_service.generate_thumbnail(comic.file_path, target_path)

            if success:
                comic.thumbnail_path = str(target_path)
                # Note: No commit needed here as batch loop handles it,
                # or flush() in import_comic handles the object state.

        except Exception as e:
            print(f"Failed to generate thumbnail for {comic.filename}: {e}")

    def _normalize_number(self, number: str) -> str:
        """
        Normalize weird comic numbers for better sorting.
        """
        if not number:
            return number

        # Handle "½" -> "0.5"
        if number == "½" or number == "1/2":
            return "0.5"

        # Handle "-1" (ensure it stays -1, though our casting handles it)
        # Handle variants if needed in future

        return number
