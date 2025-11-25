from pathlib import Path
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from datetime import datetime
import json
import os

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
    """Scans library directories and imports comics"""

    def __init__(self, library: Library, db: Session):
        self.library = library
        self.db = db
        self.supported_extensions = ['.cbz', '.cbr']
        self.tag_service = TagService(db)
        self.credit_service = CreditService(db)
        self.reading_list_service = ReadingListService(db)
        self.collection_service = CollectionService(db)
        self.image_service = ImageService()

    def scan(self, force: bool = False) -> dict:
        """
        Scan the library path and import comics

        Args:
            force: If True, scan all files even if they haven't been modified
        """

        library_path = Path(self.library.path)

        if not library_path.exists():
            return {"error": f"Library path does not exist: {self.library.path}"}

        found_comics = []
        errors = []
        imported = 0
        updated = 0
        skipped = 0

        print(f"Scanning {library_path}... (force={force})")

        # Track all file paths we've seen
        scanned_paths = set()

        # Walk through directory
        for file_path in library_path.rglob('*'):
            if file_path.suffix.lower() in self.supported_extensions:
                scanned_paths.add(str(file_path))

                try:
                    # Get file modification time
                    file_mtime = os.path.getmtime(file_path)

                    # Check if already imported
                    existing = self.db.query(Comic).filter(
                        Comic.file_path == str(file_path)
                    ).first()

                    if existing:
                        # If force=False, skip files that haven't been modified
                        if not force and existing.file_modified_at and existing.file_modified_at >= file_mtime:
                            skipped += 1
                            continue
                        else:
                            # File modified or force scan - update it
                            if force:
                                print(f"Force scanning: {file_path.name}")
                            else:
                                print(f"Updating modified comic: {file_path.name}")
                            comic = self._update_comic(existing, file_path, file_mtime)
                            updated += 1
                    else:
                        # New comic - import it
                        comic = self._import_comic(file_path, file_mtime)
                        imported += 1

                    if comic:
                        found_comics.append({
                            "id": comic.id,
                            "filename": comic.filename,
                            "series": comic.volume.series.name if comic.volume else None,
                            "pages": comic.page_count
                        })

                except Exception as e:
                    errors.append({"file": str(file_path), "error": str(e)})
                    print(f"Error processing {file_path}: {e}")

        # Find and remove comics whose files no longer exist
        deleted = self._cleanup_missing_files(scanned_paths)

        # Clean up empty reading lists
        self.reading_list_service.cleanup_empty_lists()

        # Clean up empty collections
        self.collection_service.cleanup_empty_collections()

        # Update library scan time
        self.library.last_scanned = datetime.utcnow()
        self.db.commit()

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
            "comics": found_comics[:10],  # Show first 10
            "error_details": errors[:5]  # Show first 5 errors
        }

    def _cleanup_missing_files(self, scanned_paths: set) -> int:
        """Remove comics from DB whose files no longer exist"""
        # Get all comics in this library
        all_comics = self.db.query(Comic).join(Volume).join(Series).filter(
            Series.library_id == self.library.id
        ).all()

        deleted = 0
        for comic in all_comics:
            if comic.file_path not in scanned_paths:
                print(f"Removing deleted comic: {comic.filename}")
                self.db.delete(comic)
                deleted += 1

        if deleted > 0:
            self.db.commit()

        return deleted

    def _import_comic(self, file_path: Path, file_mtime: float) -> Optional[Comic]:
        """Process and import a new comic file"""
        metadata = self._extract_metadata(file_path)

        if not metadata:
            return None

        # Get or create series
        series_name = metadata.get('series', 'Unknown Series')
        series = self._get_or_create_series(series_name)

        # Get or create volume
        volume_num = int(metadata.get('volume', 1)) if metadata.get('volume') else 1
        volume = self._get_or_create_volume(series, volume_num)

        # Create comic WITHOUT tags, credits first
        comic = Comic(
            volume_id=volume.id,
            filename=file_path.name,
            file_path=str(file_path),
            file_modified_at=file_mtime,
            page_count=metadata['page_count'],

            # Basic info
            number=metadata.get('number'),
            title=metadata.get('title'),
            summary=metadata.get('summary'),
            year=int(metadata.get('year')) if metadata.get('year') else None,
            month=int(metadata.get('month')) if metadata.get('month') else None,
            day=int(metadata.get('day')) if metadata.get('day') else None,
            web=metadata.get('web'),
            notes=metadata.get('notes'),

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

            # Full metadata as JSON
            metadata_json=json.dumps(metadata.get('raw_metadata', {}))
        )

        self.db.add(comic)
        self.db.commit()
        self.db.refresh(comic)

        # Add credits
        self.credit_service.add_credits_to_comic(comic, metadata)

        # Add the many-to-many relationships for tags
        if metadata.get('characters'):
            comic.characters = self.tag_service.get_or_create_characters(metadata.get('characters'))

        if metadata.get('teams'):
            comic.teams = self.tag_service.get_or_create_teams(metadata.get('teams'))

        if metadata.get('locations'):
            comic.locations = self.tag_service.get_or_create_locations(metadata.get('locations'))

        # Add to reading lists based on AlternateSeries
        self.reading_list_service.update_comic_reading_lists(
            comic,
            metadata.get('alternate_series'),
            metadata.get('alternate_number')
        )

        # Add to collections based on SeriesGroup
        self.collection_service.update_comic_collections(
            comic,
            metadata.get('series_group')
        )

        self.db.commit()

        # Generate thumbnail if it doesn't exist
        if not comic.thumbnail_path or not Path(comic.thumbnail_path).exists():
            self._generate_thumbnail(comic)

        print(f"Imported: {series_name} #{metadata.get('number', '?')} - {file_path.name}")

        return comic

    def _update_comic(self, comic: Comic, file_path: Path, file_mtime: float) -> Optional[Comic]:
        """Update an existing comic with new metadata"""
        metadata = self._extract_metadata(file_path)

        if not metadata:
            return None

        # Check if series/volume changed
        series_name = metadata.get('series', 'Unknown Series')
        volume_num = int(metadata.get('volume', 1)) if metadata.get('volume') else 1

        # Get or create new series/volume if changed
        series = self._get_or_create_series(series_name)
        volume = self._get_or_create_volume(series, volume_num)

        # Update ALL comic fields (set to None if not in metadata)
        comic.volume_id = volume.id
        comic.file_modified_at = file_mtime
        comic.page_count = metadata['page_count']

        # Basic info
        comic.number = metadata.get('number')
        comic.title = metadata.get('title')
        comic.summary = metadata.get('summary')
        comic.year = int(metadata.get('year')) if metadata.get('year') else None
        comic.month = int(metadata.get('month')) if metadata.get('month') else None
        comic.day = int(metadata.get('day')) if metadata.get('day') else None
        comic.web = metadata.get('web')
        comic.notes = metadata.get('notes')

        # Publishing
        comic.publisher = metadata.get('publisher')
        comic.imprint = metadata.get('imprint')
        comic.format = metadata.get('format')
        comic.series_group = metadata.get('series_group')

        # Technical (will be None if removed from metadata)
        comic.scan_information = metadata.get('scan_information')

        # Update credits (automatically clears old ones)
        self.credit_service.add_credits_to_comic(comic, metadata)

        # CLEAR existing tags first, then add new ones
        # This ensures removed tags are actually removed
        comic.characters.clear()
        comic.teams.clear()
        comic.locations.clear()

        # Now add the new tags (if any)
        if metadata.get('characters'):
            comic.characters = self.tag_service.get_or_create_characters(metadata.get('characters'))

        if metadata.get('teams'):
            comic.teams = self.tag_service.get_or_create_teams(metadata.get('teams'))

        if metadata.get('locations'):
            comic.locations = self.tag_service.get_or_create_locations(metadata.get('locations'))

        # Reading lists (will be None if removed)
        comic.alternate_series = metadata.get('alternate_series')
        comic.alternate_number = metadata.get('alternate_number')
        comic.story_arc = metadata.get('story_arc')

        # Update reading list membership
        self.reading_list_service.update_comic_reading_lists(
            comic,
            metadata.get('alternate_series'),
            metadata.get('alternate_number')
        )

        # Update collection membership
        self.collection_service.update_comic_collections(
            comic,
            metadata.get('series_group')
        )

        # Full metadata
        comic.metadata_json = json.dumps(metadata.get('raw_metadata', {}))
        comic.updated_at = datetime.utcnow()

        self.db.commit()
        self.db.refresh(comic)

        # Generate thumbnail if it doesn't exist
        if not comic.thumbnail_path or not Path(comic.thumbnail_path).exists():
            self._generate_thumbnail(comic)

        print(f"Updated: {series_name} #{metadata.get('number', '?')} - {file_path.name}")

        return comic

    def _extract_metadata(self, file_path: Path) -> Optional[Dict]:
        """Extract metadata from comic archive"""
        try:
            with ComicArchive(file_path) as archive:
                pages = archive.get_pages()

                if not pages:
                    print(f"Warning: No valid image pages found in {file_path.name}")
                    return None

                comicinfo_xml = archive.get_comicinfo()

                metadata = {'page_count': len(pages)}
                if comicinfo_xml:
                    parsed = parse_comicinfo(comicinfo_xml)
                    metadata.update(parsed)
                    metadata['raw_metadata'] = parsed

                return metadata
        except Exception as e:
            print(f"Error extracting metadata from {file_path}: {e}")
            return None

    def _get_or_create_series(self, name: str) -> Series:
        """Get existing series or create new one"""
        series = self.db.query(Series).filter(
            Series.name == name,
            Series.library_id == self.library.id
        ).first()

        if not series:
            series = Series(name=name, library_id=self.library.id)
            self.db.add(series)
            self.db.commit()
            self.db.refresh(series)

        return series

    def _get_or_create_volume(self, series: Series, volume_number: int) -> Volume:
        """Get existing volume or create new one"""
        volume = self.db.query(Volume).filter(
            Volume.series_id == series.id,
            Volume.volume_number == volume_number
        ).first()

        if not volume:
            volume = Volume(series_id=series.id, volume_number=volume_number)
            self.db.add(volume)
            self.db.commit()
            self.db.refresh(volume)

        return volume

    def _generate_thumbnail(self, comic: Comic) -> None:
        thumbnail_bytes = self.image_service.get_thumbnail(comic.file_path)
        if thumbnail_bytes:
            # Save with a proper filename
            thumbnail_filename = f"comic_{comic.id}.webp"
            # TODO: get cover path from settings instead of hardcoding here
            thumbnail_path = Path("./storage/cover") / thumbnail_filename #settings.cache_dir / thumbnail_filename
            thumbnail_path.write_bytes(thumbnail_bytes)
            comic.thumbnail_path = str(thumbnail_path)
            self.db.commit()
