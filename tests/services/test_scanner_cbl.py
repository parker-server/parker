import hashlib
from unittest.mock import MagicMock

from app.models.cbl_source import CBLSource
from app.models.comic import Volume
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.series import Series
from app.services.scanner import LibraryScanner
import app.services.cbl_source_service as cbl_source_service_module
from tests.factories import create_comic, create_library_with_root

VALID_CBL = (
    b'<?xml version="1.0"?><ReadingList><Name>Scanner CBL Event</Name>'
    b'<Books><Book Series="Scanner Series" Number="1" /></Books></ReadingList>'
)


def _build_scanner(db, tmp_path, *, name="scanner-cbl-lib"):
    library = create_library_with_root(db, name, str(tmp_path))
    db.commit()

    scanner = LibraryScanner(library, db)
    scanner.reading_list_service.cleanup_empty_lists = MagicMock()
    scanner.collection_service.cleanup_empty_collections = MagicMock()

    return scanner, library


def test_scan_parallel_discovers_and_imports_library_root_cbl_file(db, tmp_path, monkeypatch):
    managed_dir = tmp_path / "cbl_managed"
    monkeypatch.setattr(cbl_source_service_module.settings, "cbl_dir", managed_dir)

    scanner, library = _build_scanner(db, tmp_path)

    events_dir = tmp_path / "Events"
    events_dir.mkdir()
    (events_dir / "test-event.cbl").write_bytes(VALID_CBL)

    result = scanner.scan_parallel(force=False, worker_limit=1)

    assert result["errors"] == 0
    sources = db.query(CBLSource).all()
    assert len(sources) == 1
    assert sources[0].origin == "library_import"
    assert sources[0].original_filename == "Events/test-event.cbl"
    assert managed_dir.exists()

    # Re-scanning must not re-import the same file as a duplicate source.
    scanner.scan_parallel(force=False, worker_limit=1)
    assert db.query(CBLSource).count() == 1


def test_scan_parallel_rebuilds_existing_cbl_sources_after_scan(db, tmp_path, monkeypatch):
    managed_dir = tmp_path / "cbl_managed"
    managed_dir.mkdir(parents=True)
    monkeypatch.setattr(cbl_source_service_module.settings, "cbl_dir", managed_dir)

    scanner, library = _build_scanner(db, tmp_path)
    root = library.active_root

    series = Series(name="Scanner Series", library_id=library.id)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()

    comic_path = tmp_path / "scanner-1.cbz"
    comic_path.write_bytes(b"x")
    future_mtime = comic_path.stat().st_mtime + 60
    comic = create_comic(
        db, volume, root, "scanner-1.cbz",
        number="1", filename="scanner-1.cbz",
        file_modified_at=future_mtime, page_count=10,
    )

    fingerprint = hashlib.sha256(VALID_CBL).hexdigest()
    stored_path = managed_dir / f"{fingerprint}.cbl"
    stored_path.write_bytes(VALID_CBL)
    cbl_source = CBLSource(
        display_name="Scanner CBL Event",
        stored_path=str(stored_path),
        original_filename="scanner-event.cbl",
        origin="upload",
        fingerprint=fingerprint,
        last_refresh_status="never",
    )
    db.add(cbl_source)
    db.commit()

    result = scanner.scan_parallel(force=False, worker_limit=1)

    assert result["skipped"] == 1  # comic unchanged -- no multiprocessing writer invoked
    db.refresh(cbl_source)
    assert cbl_source.entry_count == 1

    reading_list = db.query(ReadingList).filter(ReadingList.source_cbl_id == cbl_source.id).first()
    assert reading_list is not None
    assert reading_list.name == "Scanner CBL Event"
    items = db.query(ReadingListItem).filter(ReadingListItem.reading_list_id == reading_list.id).all()
    assert [item.comic_id for item in items] == [comic.id]
