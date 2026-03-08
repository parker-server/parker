import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.series import Series
from app.services.scanner import LibraryScanner
import app.services.scanner as scanner_module


def _build_scanner(db, tmp_path, *, name="scanner-lib"):
    library = Library(name=name, path=str(tmp_path))
    db.add(library)
    db.commit()
    db.refresh(library)

    scanner = LibraryScanner(library, db)

    scanner.tag_service = SimpleNamespace(
        get_or_create_characters=MagicMock(return_value=[]),
        get_or_create_teams=MagicMock(return_value=[]),
        get_or_create_locations=MagicMock(return_value=[]),
        get_or_create_genres=MagicMock(return_value=[]),
    )
    scanner.credit_service = SimpleNamespace(add_credits_to_comic=MagicMock())
    scanner.reading_list_service = SimpleNamespace(
        update_comic_reading_lists=MagicMock(),
        cleanup_empty_lists=MagicMock(),
    )
    scanner.collection_service = SimpleNamespace(
        update_comic_collections=MagicMock(),
        cleanup_empty_collections=MagicMock(),
    )

    return scanner, library


def _metadata(**overrides):
    payload = {
        "series": "Series A",
        "volume": "1",
        "number": "1/2",
        "page_count": 24,
        "title": "Issue Title",
        "summary": "Summary",
        "year": "2024",
        "month": "6",
        "day": "15",
        "web": "https://example.com",
        "notes": "note",
        "age_rating": "Teen",
        "lang": "en",
        "community_rating": 4.5,
        "publisher": "Marvel",
        "imprint": "Max",
        "format": "One-Shot",
        "series_group": "Event",
        "scan_information": "scanner",
        "alternate_series": "Alt",
        "alternate_number": "A1",
        "story_arc": "Arc",
        "count": "12",
        "raw_metadata": {"a": 1},
        "characters": ["Batman"],
        "teams": ["JL"],
        "locations": ["Gotham"],
        "genre": ["Superhero"],
    }
    payload.update(overrides)
    return payload


def _fake_result_comic(comic_id, filename, pages=22, series_name="Result Series"):
    return SimpleNamespace(
        id=comic_id,
        filename=filename,
        page_count=pages,
        volume=SimpleNamespace(series=SimpleNamespace(name=series_name)),
    )


def test_scan_raises_when_library_path_missing(db, tmp_path):
    missing = tmp_path / "missing"
    scanner, library = _build_scanner(db, missing)

    with pytest.raises(FileNotFoundError):
        scanner.scan()


def test_scan_processes_skip_update_import_and_errors(db, tmp_path, monkeypatch):
    scanner, library = _build_scanner(db, tmp_path)

    skip_file = tmp_path / "skip.cbz"
    update_file = tmp_path / "update.cbz"
    import_file = tmp_path / "import.cbz"
    fail_meta_file = tmp_path / "failmeta.cbz"
    explode_file = tmp_path / "explode.cbz"

    for file_path in [skip_file, update_file, import_file, fail_meta_file, explode_file]:
        file_path.write_bytes(b"x")

    series = Series(name="Series Existing", library_id=library.id)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()

    skip_mtime = scanner_module.os.path.getmtime(skip_file)
    update_mtime = scanner_module.os.path.getmtime(update_file)

    existing_skip = Comic(
        volume_id=volume.id,
        number="1",
        filename=skip_file.name,
        file_path=str(skip_file),
        file_modified_at=skip_mtime + 100,
        page_count=10,
    )
    existing_update = Comic(
        volume_id=volume.id,
        number="2",
        filename=update_file.name,
        file_path=str(update_file),
        file_modified_at=update_mtime - 100,
        page_count=10,
    )
    db.add_all([existing_skip, existing_update])
    db.commit()

    monkeypatch.setattr(scanner, "_reconcile_sidecars", MagicMock())

    def extract_side_effect(file_path):
        name = Path(file_path).name
        if name == "failmeta.cbz":
            return None
        if name == "explode.cbz":
            raise RuntimeError("bad archive")
        return _metadata()

    monkeypatch.setattr(scanner, "_extract_metadata", MagicMock(side_effect=extract_side_effect))
    monkeypatch.setattr(scanner, "_update_comic", MagicMock(return_value=_fake_result_comic(11, "update.cbz", 30, "Upd")))
    monkeypatch.setattr(scanner, "_import_comic", MagicMock(return_value=_fake_result_comic(12, "import.cbz", 28, "Imp")))
    monkeypatch.setattr(scanner, "_cleanup_missing_files", MagicMock(return_value=2))

    monkeypatch.setattr(scanner_module.time, "time", MagicMock(side_effect=[100.0, 102.75]))

    result = scanner.scan(force=False)

    assert result["imported"] == 1
    assert result["updated"] == 1
    assert result["skipped"] == 1
    assert result["errors"] == 2
    assert result["found"] == 2
    assert result["deleted"] == 2
    assert result["elapsed"] == 2.75

    scanner.reading_list_service.cleanup_empty_lists.assert_called_once()
    scanner.collection_service.cleanup_empty_collections.assert_called_once()
    assert scanner.library.last_scanned is not None


def test_scan_force_mode_updates_existing_without_mtime_check(db, tmp_path, monkeypatch):
    scanner, library = _build_scanner(db, tmp_path, name="scanner-force-lib")

    file_path = tmp_path / "force.cbz"
    file_path.write_bytes(b"x")

    series = Series(name="Force Series", library_id=library.id)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()

    mtime = scanner_module.os.path.getmtime(file_path)
    existing = Comic(
        volume_id=volume.id,
        number="1",
        filename=file_path.name,
        file_path=str(file_path),
        file_modified_at=mtime + 9999,
        page_count=10,
    )
    db.add(existing)
    db.commit()

    monkeypatch.setattr(scanner, "_extract_metadata", MagicMock(return_value=_metadata()))
    monkeypatch.setattr(scanner, "_update_comic", MagicMock(return_value=_fake_result_comic(20, file_path.name)))
    monkeypatch.setattr(scanner, "_cleanup_missing_files", MagicMock(return_value=0))
    monkeypatch.setattr(scanner, "_reconcile_sidecars", MagicMock())

    result = scanner.scan(force=True)

    assert result["updated"] == 1
    assert result["skipped"] == 0


def test_scan_commits_when_batch_size_threshold_hit(db, tmp_path, monkeypatch):
    scanner, _ = _build_scanner(db, tmp_path, name="scanner-batch-lib")

    for i in range(50):
        (tmp_path / f"batch-{i}.cbz").write_bytes(b"x")

    monkeypatch.setattr(scanner, "_extract_metadata", MagicMock(return_value=_metadata()))
    monkeypatch.setattr(
        scanner,
        "_import_comic",
        MagicMock(side_effect=[_fake_result_comic(i + 1, f"batch-{i}.cbz") for i in range(50)]),
    )
    monkeypatch.setattr(scanner, "_cleanup_missing_files", MagicMock(return_value=0))
    monkeypatch.setattr(scanner, "_reconcile_sidecars", MagicMock())

    debug_logger = MagicMock()
    scanner.logger.debug = debug_logger

    result = scanner.scan()

    assert result["imported"] == 50
    debug_msgs = [call.args[0] for call in debug_logger.call_args_list if call.args]
    assert any("Committing batch of 50 items" in msg for msg in debug_msgs)


def test_cleanup_missing_files_deletes_and_commits(db, tmp_path):
    scanner, library = _build_scanner(db, tmp_path, name="scanner-cleanup-lib")

    series = Series(name="Cleanup Series", library_id=library.id)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()

    keep = Comic(volume_id=volume.id, number="1", filename="keep.cbz", file_path=str(tmp_path / "keep.cbz"), page_count=10)
    gone = Comic(volume_id=volume.id, number="2", filename="gone.cbz", file_path=str(tmp_path / "gone.cbz"), page_count=10)
    db.add_all([keep, gone])
    db.commit()

    deleted = scanner._cleanup_missing_files({str(tmp_path / "keep.cbz")}, {keep.file_path: keep, gone.file_path: gone})

    assert deleted == 1
    assert db.get(Comic, gone.id) is None
    assert db.get(Comic, keep.id) is not None


def test_import_comic_sets_fields_and_calls_services(db, tmp_path, monkeypatch):
    scanner, library = _build_scanner(db, tmp_path, name="scanner-import-lib")

    series = Series(name="Import Series", library_id=library.id)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()

    monkeypatch.setattr(scanner, "_get_or_create_series", lambda name: series)
    monkeypatch.setattr(scanner, "_get_or_create_volume", lambda s, v, p: volume)

    scanner.tag_service.get_or_create_characters.return_value = []
    scanner.tag_service.get_or_create_teams.return_value = []
    scanner.tag_service.get_or_create_locations.return_value = []
    scanner.tag_service.get_or_create_genres.return_value = []

    file_path = tmp_path / "import-me.cbz"
    metadata = _metadata(number="1/2")

    comic = scanner._import_comic(file_path, 123.4, 5678, metadata)

    assert comic.id is not None
    assert comic.volume_id == volume.id
    assert comic.number == "0.5"
    assert comic.year == 2024
    assert comic.month == 6
    assert comic.day == 15
    assert comic.count == 12
    assert comic.is_dirty is True
    assert json.loads(comic.metadata_json) == {"a": 1}

    scanner.credit_service.add_credits_to_comic.assert_called_once_with(comic, metadata)
    scanner.reading_list_service.update_comic_reading_lists.assert_called_once()
    scanner.collection_service.update_comic_collections.assert_called_once()


def test_update_comic_updates_fields_and_clears_tags(db, tmp_path, monkeypatch):
    scanner, library = _build_scanner(db, tmp_path, name="scanner-update-lib")

    old_series = Series(name="Old Series", library_id=library.id)
    old_volume = Volume(series=old_series, volume_number=1)
    new_series = Series(name="New Series", library_id=library.id)
    new_volume = Volume(series=new_series, volume_number=2)
    db.add_all([old_series, old_volume, new_series, new_volume])
    db.flush()

    comic = Comic(
        volume_id=old_volume.id,
        number="1",
        title="Old",
        filename="old.cbz",
        file_path=str(tmp_path / "old.cbz"),
        page_count=10,
    )
    db.add(comic)
    db.flush()

    monkeypatch.setattr(scanner, "_get_or_create_series", lambda name: new_series)
    monkeypatch.setattr(scanner, "_get_or_create_volume", lambda s, v, p: new_volume)

    metadata = _metadata(number="2", title="Updated", count="3")

    updated = scanner._update_comic(comic, tmp_path / "old.cbz", 222.2, 3333, metadata)

    assert updated is comic
    assert comic.volume_id == new_volume.id
    assert comic.number == "2"
    assert comic.title == "Updated"
    assert comic.file_modified_at == 222.2
    assert comic.file_size == 3333
    assert comic.count == 3
    assert comic.is_dirty is True

    scanner.credit_service.add_credits_to_comic.assert_called_once_with(comic, metadata)
    scanner.reading_list_service.update_comic_reading_lists.assert_called_once()
    scanner.collection_service.update_comic_collections.assert_called_once()


def test_extract_metadata_paths(monkeypatch, db, tmp_path):
    scanner, _ = _build_scanner(db, tmp_path, name="scanner-extract-lib")

    class ArchiveNoPages:
        def __init__(self, _):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_pages(self):
            return []

        def get_comicinfo(self):
            return None

    monkeypatch.setattr(scanner_module, "ComicArchive", ArchiveNoPages)
    assert scanner._extract_metadata(tmp_path / "no-pages.cbz") is None

    class ArchivePlain:
        def __init__(self, _):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_pages(self):
            return ["1", "2", "3"]

        def get_comicinfo(self):
            return None

    monkeypatch.setattr(scanner_module, "ComicArchive", ArchivePlain)
    assert scanner._extract_metadata(tmp_path / "plain.cbz") == {"page_count": 3}

    class ArchiveWithXml:
        def __init__(self, _):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_pages(self):
            return ["a", "b"]

        def get_comicinfo(self):
            return "<xml/>"

    monkeypatch.setattr(scanner_module, "ComicArchive", ArchiveWithXml)
    monkeypatch.setattr(scanner_module, "parse_comicinfo", lambda xml: {"title": "X", "page_count": 999})
    data = scanner._extract_metadata(tmp_path / "with-xml.cbz")
    assert data["title"] == "X"
    assert data["page_count"] == 2
    assert data["raw_metadata"] == {"title": "X", "page_count": 999}

    class ArchiveBoom:
        def __init__(self, _):
            raise RuntimeError("archive fail")

    monkeypatch.setattr(scanner_module, "ComicArchive", ArchiveBoom)
    assert scanner._extract_metadata(tmp_path / "boom.cbz") is None


def test_get_or_create_series_caching_and_sidecar(db, tmp_path, monkeypatch):
    scanner, library = _build_scanner(db, tmp_path, name="scanner-series-lib")

    existing = Series(name="Existing", library_id=library.id)
    db.add(existing)
    db.commit()

    assert scanner._get_or_create_series("Existing").id == existing.id
    assert scanner._get_or_create_series("Existing").id == existing.id  # cache hit

    sidecar = MagicMock(return_value="Series Summary")
    monkeypatch.setattr(scanner_module.SidecarService, "get_summary_from_disk", sidecar)

    created = scanner._get_or_create_series("Brand New")
    assert created.name == "Brand New"
    assert created.summary_override == "Series Summary"

    sidecar.reset_mock()
    unknown = scanner._get_or_create_series("Unknown Series")
    assert unknown.name == "Unknown Series"
    sidecar.assert_not_called()


def test_get_or_create_volume_caching_and_sidecar(db, tmp_path, monkeypatch):
    scanner, library = _build_scanner(db, tmp_path, name="scanner-volume-lib")

    series = Series(name="Volume Series", library_id=library.id)
    existing_volume = Volume(series=series, volume_number=1)
    db.add_all([series, existing_volume])
    db.commit()

    first = scanner._get_or_create_volume(series, 1, tmp_path / "Volume Series" / "v1" / "a.cbz")
    second = scanner._get_or_create_volume(series, 1, tmp_path / "Volume Series" / "v1" / "b.cbz")
    assert first.id == existing_volume.id
    assert second.id == existing_volume.id

    sidecar = MagicMock(return_value="Volume Summary")
    monkeypatch.setattr(scanner_module.SidecarService, "get_summary_from_disk", sidecar)

    created = scanner._get_or_create_volume(series, 2, tmp_path / "Volume Series" / "v2" / "a.cbz")
    assert created.volume_number == 2
    assert created.summary_override == "Volume Summary"

    sidecar.reset_mock()
    root_created = scanner._get_or_create_volume(series, 3, tmp_path / "root.cbz")
    assert root_created.volume_number == 3
    sidecar.assert_not_called()


def test_normalize_number():
    dummy = object.__new__(LibraryScanner)

    assert dummy._normalize_number(None) is None
    assert dummy._normalize_number("") == ""
    assert dummy._normalize_number("½") == "0.5"
    assert dummy._normalize_number("1/2") == "0.5"
    assert dummy._normalize_number("-1") == "-1"


def test_resolve_sidecar_from_parents_paths(db, tmp_path, monkeypatch):
    scanner, _ = _build_scanner(db, tmp_path, name="scanner-sidecar-lib")

    series_dir = tmp_path / "Series"
    issue_dir = series_dir / "Issue"

    def lookup(path, entity_type):
        p = Path(path)
        if p == issue_dir:
            return None
        if p == series_dir:
            return "Parent Summary"
        return None

    monkeypatch.setattr(scanner_module.SidecarService, "get_summary_from_disk", lookup)

    assert scanner._resolve_sidecar_from_parents(issue_dir, "series", tmp_path) == "Parent Summary"

    monkeypatch.setattr(scanner_module.SidecarService, "get_summary_from_disk", lambda path, entity: None)
    assert scanner._resolve_sidecar_from_parents(issue_dir, "series", tmp_path) is None

    assert scanner._resolve_sidecar_from_parents(Path("C:/"), "series", Path("D:/not-used")) is None


def test_reconcile_sidecars_guard_paths(db, tmp_path, monkeypatch):
    scanner, library = _build_scanner(db, tmp_path, name="scanner-reconcile-lib")

    root_file = tmp_path / "root.cbz"
    scanner._reconcile_sidecars(root_file, {})

    nested = tmp_path / "Series" / "Issue" / "a.cbz"
    scanner.reconciled_folders.add(str(nested.parent))
    scanner._reconcile_sidecars(nested, {})

    scanner.reconciled_folders.clear()
    scanner._reconcile_sidecars(nested, {})

    assert str(nested.parent) not in scanner.reconciled_folders


def test_reconcile_sidecars_updates_series_and_volume_once(db, tmp_path, monkeypatch):
    scanner, library = _build_scanner(db, tmp_path, name="scanner-reconcile-update-lib")

    series = Series(name="Recon Series", library_id=library.id, summary_override="old-series")
    volume = Volume(series=series, volume_number=1, summary_override="old-volume")
    comic = Comic(
        volume=volume,
        number="1",
        filename="a.cbz",
        file_path=str(tmp_path / "Recon Series" / "Issue" / "a.cbz"),
        page_count=10,
    )
    db.add_all([series, volume, comic])
    db.commit()

    file_path = Path(comic.file_path)

    resolver = MagicMock(side_effect=["new-volume", "new-series"])
    monkeypatch.setattr(scanner, "_resolve_sidecar_from_parents", resolver)

    scanner._reconcile_sidecars(file_path, {str(file_path): comic})

    assert volume.summary_override == "new-volume"
    assert series.summary_override == "new-series"
    assert volume.id in scanner.reconciled_volumes
    assert series.id in scanner.reconciled_series
    assert str(file_path.parent) in scanner.reconciled_folders

    resolver.reset_mock()
    scanner._reconcile_sidecars(file_path, {str(file_path): comic})
    resolver.assert_not_called()


