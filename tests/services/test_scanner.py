from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.models.comic import Comic, Volume
from app.models.library_root import LibraryRoot
from app.models.series import Series
from app.services.scanner import LibraryScanner
import app.services.scanner as scanner_module
from tests.factories import create_comic, create_library_with_root


def _build_scanner(db, tmp_path, *, name="scanner-lib"):
    library = create_library_with_root(db, name, str(tmp_path))
    db.commit()

    scanner = LibraryScanner(library, db)
    scanner.reading_list_service.cleanup_empty_lists = MagicMock()
    scanner.collection_service.cleanup_empty_collections = MagicMock()

    return scanner, library


def test_scan_parallel_raises_when_library_path_missing(db, tmp_path):
    missing = tmp_path / "missing"
    scanner, _ = _build_scanner(db, missing)

    with pytest.raises(FileNotFoundError):
        scanner.scan_parallel()


def test_scan_parallel_raises_before_cleanup_when_any_active_root_missing(db, tmp_path):
    first_root_path = tmp_path / "first"
    missing_root_path = tmp_path / "missing"
    first_root_path.mkdir()

    scanner, library = _build_scanner(db, first_root_path, name="scanner-missing-root-lib")
    db.add(LibraryRoot(library_id=library.id, path=str(missing_root_path), is_active=True))
    db.commit()

    scanner._cleanup_missing_files = MagicMock()

    with pytest.raises(FileNotFoundError, match="Library path does not exist"):
        scanner.scan_parallel()

    scanner._cleanup_missing_files.assert_not_called()


def test_scan_parallel_cleans_up_missing_files_across_all_active_roots(db, tmp_path):
    first_root_path = tmp_path / "first"
    second_root_path = tmp_path / "second"
    first_root_path.mkdir()
    second_root_path.mkdir()

    scanner, library = _build_scanner(db, first_root_path, name="scanner-multi-root-lib")
    first_root = library.active_root
    second_root = LibraryRoot(library_id=library.id, path=str(second_root_path), is_active=True)
    db.add(second_root)
    db.flush()

    series = Series(name="Multi Root Cleanup", library_id=library.id)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()

    first_file = first_root_path / "first.cbz"
    second_file = second_root_path / "second.cbz"
    first_file.write_bytes(b"first")
    second_file.write_bytes(b"second")

    future_mtime = max(first_file.stat().st_mtime, second_file.stat().st_mtime) + 60
    first_comic = create_comic(
        db,
        volume,
        first_root,
        "first.cbz",
        number="1",
        filename="first.cbz",
        file_modified_at=future_mtime,
        page_count=10,
    )
    second_comic = create_comic(
        db,
        volume,
        second_root,
        "second.cbz",
        number="2",
        filename="second.cbz",
        file_modified_at=future_mtime,
        page_count=10,
    )
    missing_comic = create_comic(
        db,
        volume,
        second_root,
        "missing.cbz",
        number="3",
        filename="missing.cbz",
        file_modified_at=future_mtime,
        page_count=10,
    )
    db.commit()

    result = scanner.scan_parallel(force=False, worker_limit=1)

    assert result["skipped"] == 2
    assert result["deleted"] == 1
    assert db.get(Comic, first_comic.id) is not None
    assert db.get(Comic, second_comic.id) is not None
    assert db.get(Comic, missing_comic.id) is None

    db.refresh(first_root)
    db.refresh(second_root)
    assert first_root.last_scanned_at is not None
    assert second_root.last_scanned_at is not None


def test_cleanup_missing_files_deletes_and_commits(db, tmp_path):
    scanner, library = _build_scanner(db, tmp_path, name="scanner-cleanup-lib")
    root = library.active_root

    series = Series(name="Cleanup Series", library_id=library.id)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()

    keep = create_comic(db, volume, root, "keep.cbz", number="1", filename="keep.cbz", page_count=10)
    gone = create_comic(db, volume, root, "gone.cbz", number="2", filename="gone.cbz", page_count=10)
    db.commit()

    deleted = scanner._cleanup_missing_files(
        {(root.id, "keep.cbz")},
        {(root.id, "keep.cbz"): keep, (root.id, "gone.cbz"): gone},
    )

    assert deleted == 1
    assert db.get(Comic, gone.id) is None
    assert db.get(Comic, keep.id) is not None


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


def test_reconcile_sidecars_guard_paths(db, tmp_path):
    scanner, _ = _build_scanner(db, tmp_path, name="scanner-reconcile-lib")

    root_file = tmp_path / "root.cbz"
    scanner._reconcile_sidecars(root_file, None, str(tmp_path))

    nested = tmp_path / "Series" / "Issue" / "a.cbz"
    scanner.reconciled_folders.add(str(nested.parent))
    scanner._reconcile_sidecars(nested, None, str(tmp_path))

    scanner.reconciled_folders.clear()
    scanner._reconcile_sidecars(nested, None, str(tmp_path))

    assert str(nested.parent) not in scanner.reconciled_folders


def test_reconcile_sidecars_updates_series_and_volume_once(db, tmp_path, monkeypatch):
    scanner, library = _build_scanner(db, tmp_path, name="scanner-reconcile-update-lib")
    root = library.active_root

    series = Series(name="Recon Series", library_id=library.id, summary_override="old-series")
    volume = Volume(series=series, volume_number=1, summary_override="old-volume")
    comic = Comic(
        volume=volume,
        number="1",
        filename="a.cbz",
        library_root_id=root.id,
        relative_path="Recon Series/Issue/a.cbz",
        page_count=10,
    )
    db.add_all([series, volume, comic])
    db.commit()

    file_path = Path(root.path) / "Recon Series" / "Issue" / "a.cbz"

    resolver = MagicMock(side_effect=["new-volume", "new-series"])
    monkeypatch.setattr(scanner, "_resolve_sidecar_from_parents", resolver)

    scanner._reconcile_sidecars(file_path, comic, root.path)

    assert volume.summary_override == "new-volume"
    assert series.summary_override == "new-series"
    assert volume.id in scanner.reconciled_volumes
    assert series.id in scanner.reconciled_series
    assert str(file_path.parent) in scanner.reconciled_folders

    resolver.reset_mock()
    scanner._reconcile_sidecars(file_path, comic, root.path)
    resolver.assert_not_called()


def test_scan_parallel_clears_reconciliation_caches_each_run(db, tmp_path):
    scanner, library = _build_scanner(db, tmp_path, name="scanner-cache-reset-lib")
    root = library.active_root

    comic_path = tmp_path / "Series" / "Issue" / "reset.cbz"
    comic_path.parent.mkdir(parents=True, exist_ok=True)
    comic_path.write_bytes(b"x")

    series = Series(name="Reset Series", library_id=library.id)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()

    comic = create_comic(
        db, volume, root, "Series/Issue/reset.cbz",
        number="1",
        filename=comic_path.name,
        file_modified_at=comic_path.stat().st_mtime + 60,
        page_count=10,
    )
    db.commit()

    scanner.reconciled_folders.add("stale-folder")
    scanner.reconciled_volumes.add(999)
    scanner.reconciled_series.add(888)

    observed = []

    def reconcile_stub(*_args, **_kwargs):
        observed.append(
            (
                set(scanner.reconciled_folders),
                set(scanner.reconciled_volumes),
                set(scanner.reconciled_series),
            )
        )

    scanner._reconcile_sidecars = reconcile_stub
    scanner._cleanup_missing_files = lambda *_args, **_kwargs: 0
    scanner.reading_list_service.cleanup_empty_lists = lambda: None
    scanner.collection_service.cleanup_empty_collections = lambda: None

    result = scanner.scan_parallel(force=False, worker_limit=1)

    assert result["skipped"] == 1
    assert observed == [(set(), set(), set())]
