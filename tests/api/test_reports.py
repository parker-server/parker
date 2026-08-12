from app.models.comic import Volume
from app.models.series import Series
from tests.factories import create_comic, create_library_with_root


def _create_library_series_volume(db, *, lib_name: str, series_name: str, volume_number: int = 1):
    lib = create_library_with_root(db, lib_name, f"/tmp/{lib_name}")
    series = Series(name=series_name, library=lib)
    volume = Volume(series=series, volume_number=volume_number)
    db.add_all([series, volume])
    db.flush()
    return lib, series, volume


def test_missing_issues_report_detects_gaps_and_skips_standalone(admin_client, db):
    missing_lib, _, volume_missing = _create_library_series_volume(
        db,
        lib_name="reports-missing-lib",
        series_name="Reports Missing Series",
    )
    missing_root = missing_lib.active_root

    create_comic(
        db, volume_missing, missing_root, "missing-1.cbz",
        number="1", filename="missing-1.cbz", file_size=100, page_count=20, count=5,
    )
    create_comic(
        db, volume_missing, missing_root, "missing-2.cbz",
        number="2", filename="missing-2.cbz", file_size=120, page_count=22, count=5,
    )
    create_comic(
        db, volume_missing, missing_root, "missing-4.cbz",
        number="4", filename="missing-4.cbz", file_size=140, page_count=24, count=5,
    )

    standalone_lib, _, standalone_volume = _create_library_series_volume(
        db,
        lib_name="reports-standalone-lib",
        series_name="Reports Standalone Series",
    )
    standalone_root = standalone_lib.active_root

    create_comic(
        db, standalone_volume, standalone_root, "standalone-annual.cbz",
        number="1", format="annual", filename="standalone-annual.cbz",
        file_size=200, page_count=30, count=1,
    )
    db.commit()

    response = admin_client.get("/api/reports/missing?page=1&size=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["missing"] == "3, 5"
    assert payload["items"][0]["owned"] == "3 / 5"


def test_storage_reports_for_libraries_and_series(admin_client, db):
    lib_a, series_a, volume_a = _create_library_series_volume(
        db,
        lib_name="reports-storage-lib-a",
        series_name="Reports Storage Series A",
    )
    lib_b, series_b, volume_b = _create_library_series_volume(
        db,
        lib_name="reports-storage-lib-b",
        series_name="Reports Storage Series B",
    )
    root_a = lib_a.active_root
    root_b = lib_b.active_root

    create_comic(
        db, volume_a, root_a, "storage-a-1.cbz",
        number="1", filename="storage-a-1.cbz", file_size=900, page_count=20,
    )
    create_comic(
        db, volume_a, root_a, "storage-a-2.cbz",
        number="2", filename="storage-a-2.cbz", file_size=100, page_count=25,
    )
    create_comic(
        db, volume_b, root_b, "storage-b-1.cbz",
        number="1", filename="storage-b-1.cbz", file_size=400, page_count=18,
    )
    db.commit()

    by_library = admin_client.get("/api/reports/storage/libraries")
    assert by_library.status_code == 200
    libs = by_library.json()
    assert libs[0]["library"] == "reports-storage-lib-a"
    assert libs[0]["issue_count"] == 2

    by_series = admin_client.get("/api/reports/storage/series?limit=1")
    assert by_series.status_code == 200
    series_rows = by_series.json()
    assert len(series_rows) == 1
    assert series_rows[0]["id"] == series_a.id
    assert series_rows[0]["issues"] == 2


def test_format_report_counts_known_extensions(admin_client, db):
    lib, _, volume = _create_library_series_volume(
        db,
        lib_name="reports-formats-lib",
        series_name="Reports Formats Series",
    )
    root = lib.active_root

    create_comic(db, volume, root, "fmt1.cbz", number="1", filename="fmt1.cbz")
    create_comic(db, volume, root, "fmt2.cbr", number="2", filename="fmt2.cbr")
    create_comic(db, volume, root, "fmt3.pdf", number="3", filename="fmt3.pdf")
    create_comic(db, volume, root, "fmt4.epub", number="4", filename="fmt4.epub")
    db.commit()

    response = admin_client.get("/api/reports/storage/formats")

    assert response.status_code == 200
    rows = {item["format"]: item["count"] for item in response.json()}
    assert rows["CBZ (Zip)"] == 1
    assert rows["CBR (Rar)"] == 1
    assert rows["PDF"] == 1
    assert rows["EPUB"] == 1


def test_metadata_health_empty_returns_perfect_score(admin_client):
    response = admin_client.get("/api/reports/metadata")

    assert response.status_code == 200
    assert response.json() == {"score": 100, "issues": []}


def test_metadata_health_populated_returns_issue_details(admin_client, db):
    lib, _, volume = _create_library_series_volume(
        db,
        lib_name="reports-metadata-lib",
        series_name="Unknown Series",
    )
    root = lib.active_root

    create_comic(
        db, volume, root, "meta-1.cbz",
        number="1", title="Meta One", filename="meta-1.cbz", page_count=1,
        summary="", year=None, publisher="", web="",
    )
    db.commit()

    response = admin_client.get("/api/reports/metadata")

    assert response.status_code == 200
    payload = response.json()
    labels = {item["label"] for item in payload["details"]}
    assert payload["total_comics"] == 1
    assert "Unknown Series (Scan Fallback)" in labels
    assert "Missing Summary" in labels
    assert "Missing Release Year" in labels
    assert "Missing Publisher" in labels
    assert "Missing Web/Wiki Link" in labels
    assert "Suspect Low Page Count (< 3)" in labels


def test_duplicates_report_empty_and_grouped(admin_client, db):
    empty = admin_client.get("/api/reports/duplicates?page=1&size=10")
    assert empty.status_code == 200
    assert empty.json()["total"] == 0

    lib, _, volume = _create_library_series_volume(
        db,
        lib_name="reports-dupes-lib",
        series_name="Reports Dupes Series",
    )
    root = lib.active_root

    create_comic(
        db, volume, root, "dupe-a.cbz",
        number="10", format="annual", filename="dupe-a.cbz", file_size=111,
    )
    create_comic(
        db, volume, root, "dupe-b.cbz",
        number="10", format="annual", filename="dupe-b.cbz", file_size=222,
    )
    db.commit()

    grouped = admin_client.get("/api/reports/duplicates?page=1&size=10")
    assert grouped.status_code == 200
    payload = grouped.json()
    assert payload["total"] == 1
    assert payload["items"][0]["number"] == "10"
    assert payload["items"][0]["format"] == "annual"
    assert len(payload["items"][0]["files"]) == 2


def test_corrupt_files_report_filters_and_paginates(admin_client, db):
    lib, _, volume = _create_library_series_volume(
        db,
        lib_name="reports-corrupt-lib",
        series_name="Reports Corrupt Series",
    )
    root = lib.active_root

    create_comic(
        db, volume, root, "corrupt-1.cbz",
        number="1", title="Corrupt One", filename="corrupt-1.cbz", file_size=10, page_count=1,
    )
    create_comic(
        db, volume, root, "corrupt-2.cbz",
        number="2", title="Corrupt Two", filename="corrupt-2.cbz", file_size=20, page_count=2,
    )
    create_comic(
        db, volume, root, "healthy-3.cbz",
        number="3", title="Healthy Three", filename="healthy-3.cbz", file_size=30, page_count=3,
    )
    create_comic(
        db, volume, root, "unscanned-4.cbz",
        number="4", title="Unscanned Four", filename="unscanned-4.cbz", file_size=40, page_count=0,
    )
    db.commit()

    response = admin_client.get("/api/reports/corrupt?page=1&size=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert len(payload["items"]) == 1
    assert payload["items"][0]["page_count"] in [1, 2]
