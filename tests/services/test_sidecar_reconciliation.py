import logging
import os

from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.series import Series
from app.services.scanner import LibraryScanner
from app.services.sidecar_service import SidecarService


def _write_dummy_comic(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not-a-real-archive")


def _create_existing_comic(db, volume_id, file_path, number):
    mtime = os.path.getmtime(file_path)
    comic = Comic(
        volume_id=volume_id,
        filename=file_path.name,
        file_path=str(file_path),
        file_modified_at=mtime + 60,  # Ensure scanner skips metadata extraction path
        file_size=file_path.stat().st_size,
        page_count=24,
        number=str(number),
        title=f"Issue {number}",
    )
    db.add(comic)
    db.flush()
    return comic


def _setup_library_series_volume(db, library_path):
    library = Library(name="Test Library", path=str(library_path))
    db.add(library)
    db.flush()

    series = Series(name="Hawk and Dove", library_id=library.id, summary_override="old-series")
    db.add(series)
    db.flush()

    volume = Volume(series_id=series.id, volume_number=3, summary_override="old-volume")
    db.add(volume)
    db.commit()

    return library, series, volume


def test_sidecar_reconcile_from_parent_folder_for_deep_file(db, tmp_path):
    library_path = tmp_path / "DC"
    series_path = library_path / "Hawk and Dove"
    volume_path = series_path / "Vol 3"
    deep_path = volume_path / "Extras" / "Bonus"

    # Sidecars live at container folders (not at deep leaf folder)
    expected_series_summary = "Series sidecar summary"
    expected_volume_summary = "Volume sidecar summary"
    series_path.mkdir(parents=True, exist_ok=True)
    volume_path.mkdir(parents=True, exist_ok=True)
    (series_path / "series.txt").write_text(expected_series_summary, encoding="utf-8")
    (volume_path / "volume.txt").write_text(expected_volume_summary, encoding="utf-8")

    library, series, volume = _setup_library_series_volume(db, library_path)

    comic_file = deep_path / "hawk-and-dove-03.cbz"
    _write_dummy_comic(comic_file)
    _create_existing_comic(db, volume.id, comic_file, number=1)
    db.commit()

    scanner = LibraryScanner(library, db)
    result = scanner.scan(force=False)

    db.expire_all()
    refreshed_series = db.query(Series).filter(Series.id == series.id).first()
    refreshed_volume = db.query(Volume).filter(Volume.id == volume.id).first()

    assert result["errors"] == 0
    assert result["imported"] == 0
    assert result["updated"] == 0
    assert result["skipped"] == 1
    assert refreshed_series.summary_override == expected_series_summary
    assert refreshed_volume.summary_override == expected_volume_summary


def test_sidecar_reconcile_logs_once_per_entity_when_multiple_subfolders(db, tmp_path, caplog):
    library_path = tmp_path / "DC"
    series_path = library_path / "Hawk and Dove"
    volume_path = series_path / "Vol 3"
    deep_path = volume_path / "Extras"

    expected_series_summary = "Updated series summary"
    expected_volume_summary = "Updated volume summary"
    series_path.mkdir(parents=True, exist_ok=True)
    volume_path.mkdir(parents=True, exist_ok=True)
    deep_path.mkdir(parents=True, exist_ok=True)
    (series_path / "series.txt").write_text(expected_series_summary, encoding="utf-8")
    (volume_path / "volume.txt").write_text(expected_volume_summary, encoding="utf-8")

    library, series, volume = _setup_library_series_volume(db, library_path)

    comic_a = volume_path / "hawk-and-dove-03-a.cbz"
    comic_b = deep_path / "hawk-and-dove-03-b.cbz"
    _write_dummy_comic(comic_a)
    _write_dummy_comic(comic_b)
    _create_existing_comic(db, volume.id, comic_a, number=1)
    _create_existing_comic(db, volume.id, comic_b, number=2)
    db.commit()

    caplog.set_level(logging.INFO, logger="app.services.scanner")

    scanner = LibraryScanner(library, db)
    result = scanner.scan(force=False)

    volume_logs = [
        rec.message
        for rec in caplog.records
        if rec.name == "app.services.scanner"
        and rec.message == "Sidecar: Updated Volume 3 summary for Series 'Hawk and Dove'."
    ]
    series_logs = [
        rec.message
        for rec in caplog.records
        if rec.name == "app.services.scanner"
        and rec.message == "Sidecar: Updated Series 'Hawk and Dove' summary."
    ]

    db.expire_all()
    refreshed_series = db.query(Series).filter(Series.id == series.id).first()
    refreshed_volume = db.query(Volume).filter(Volume.id == volume.id).first()

    assert result["errors"] == 0
    assert result["skipped"] == 2
    assert len(volume_logs) == 1
    assert len(series_logs) == 1
    assert refreshed_series.summary_override == expected_series_summary
    assert refreshed_volume.summary_override == expected_volume_summary


def test_sidecar_txt_decode_fallback_supports_utf16_and_cp1252(tmp_path):
    folder = tmp_path / "sidecars"
    folder.mkdir(parents=True, exist_ok=True)

    utf16_text = "Resume from UTF-16"
    (folder / "series.txt").write_text(utf16_text, encoding="utf-16")
    assert SidecarService.get_summary_from_disk(folder, "series") == utf16_text

    cp1252_text = "A\u2013B"
    (folder / "volume.txt").write_bytes(cp1252_text.encode("cp1252"))
    assert SidecarService.get_summary_from_disk(folder, "volume") == cp1252_text