from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.series import Series


def _create_library_series_volume(db, *, lib_name: str, series_name: str, volume_number: int = 1):
    lib = Library(name=lib_name, path=f"/tmp/{lib_name}")
    series = Series(name=series_name, library=lib)
    volume = Volume(series=series, volume_number=volume_number)
    db.add_all([lib, series, volume])
    db.flush()
    return lib, series, volume


def test_missing_issues_report_detects_gaps_and_skips_standalone(admin_client, db):
    _, _, volume_missing = _create_library_series_volume(
        db,
        lib_name="reports-missing-lib",
        series_name="Reports Missing Series",
    )

    db.add_all([
        Comic(
            volume_id=volume_missing.id,
            number="1",
            filename="missing-1.cbz",
            file_path="/tmp/reports-missing-1.cbz",
            file_size=100,
            page_count=20,
            count=5,
        ),
        Comic(
            volume_id=volume_missing.id,
            number="2",
            filename="missing-2.cbz",
            file_path="/tmp/reports-missing-2.cbz",
            file_size=120,
            page_count=22,
            count=5,
        ),
        Comic(
            volume_id=volume_missing.id,
            number="4",
            filename="missing-4.cbz",
            file_path="/tmp/reports-missing-4.cbz",
            file_size=140,
            page_count=24,
            count=5,
        ),
    ])

    _, _, standalone_volume = _create_library_series_volume(
        db,
        lib_name="reports-standalone-lib",
        series_name="Reports Standalone Series",
    )

    db.add(
        Comic(
            volume_id=standalone_volume.id,
            number="1",
            format="annual",
            filename="standalone-annual.cbz",
            file_path="/tmp/reports-standalone-annual.cbz",
            file_size=200,
            page_count=30,
            count=1,
        )
    )
    db.commit()

    response = admin_client.get("/api/reports/missing?page=1&size=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["missing"] == "3, 5"
    assert payload["items"][0]["owned"] == "3 / 5"


def test_storage_reports_for_libraries_and_series(admin_client, db):
    _, series_a, volume_a = _create_library_series_volume(
        db,
        lib_name="reports-storage-lib-a",
        series_name="Reports Storage Series A",
    )
    _, series_b, volume_b = _create_library_series_volume(
        db,
        lib_name="reports-storage-lib-b",
        series_name="Reports Storage Series B",
    )

    db.add_all([
        Comic(
            volume_id=volume_a.id,
            number="1",
            filename="storage-a-1.cbz",
            file_path="/tmp/reports-storage-a-1.cbz",
            file_size=900,
            page_count=20,
        ),
        Comic(
            volume_id=volume_a.id,
            number="2",
            filename="storage-a-2.cbz",
            file_path="/tmp/reports-storage-a-2.cbz",
            file_size=100,
            page_count=25,
        ),
        Comic(
            volume_id=volume_b.id,
            number="1",
            filename="storage-b-1.cbz",
            file_path="/tmp/reports-storage-b-1.cbz",
            file_size=400,
            page_count=18,
        ),
    ])
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
    _, _, volume = _create_library_series_volume(
        db,
        lib_name="reports-formats-lib",
        series_name="Reports Formats Series",
    )

    db.add_all([
        Comic(volume_id=volume.id, number="1", filename="fmt1.cbz", file_path="/tmp/reports-fmt1.cbz"),
        Comic(volume_id=volume.id, number="2", filename="fmt2.cbr", file_path="/tmp/reports-fmt2.cbr"),
        Comic(volume_id=volume.id, number="3", filename="fmt3.pdf", file_path="/tmp/reports-fmt3.pdf"),
        Comic(volume_id=volume.id, number="4", filename="fmt4.epub", file_path="/tmp/reports-fmt4.epub"),
    ])
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
    _, _, volume = _create_library_series_volume(
        db,
        lib_name="reports-metadata-lib",
        series_name="Unknown Series",
    )

    db.add(
        Comic(
            volume_id=volume.id,
            number="1",
            title="Meta One",
            filename="meta-1.cbz",
            file_path="/tmp/reports-meta-1.cbz",
            page_count=1,
            summary="",
            year=None,
            publisher="",
            web="",
        )
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

    _, _, volume = _create_library_series_volume(
        db,
        lib_name="reports-dupes-lib",
        series_name="Reports Dupes Series",
    )

    db.add_all([
        Comic(
            volume_id=volume.id,
            number="10",
            format="annual",
            filename="dupe-a.cbz",
            file_path="/tmp/reports-dupe-a.cbz",
            file_size=111,
        ),
        Comic(
            volume_id=volume.id,
            number="10",
            format="annual",
            filename="dupe-b.cbz",
            file_path="/tmp/reports-dupe-b.cbz",
            file_size=222,
        ),
    ])
    db.commit()

    grouped = admin_client.get("/api/reports/duplicates?page=1&size=10")
    assert grouped.status_code == 200
    payload = grouped.json()
    assert payload["total"] == 1
    assert payload["items"][0]["number"] == "10"
    assert payload["items"][0]["format"] == "annual"
    assert len(payload["items"][0]["files"]) == 2


def test_corrupt_files_report_filters_and_paginates(admin_client, db):
    _, _, volume = _create_library_series_volume(
        db,
        lib_name="reports-corrupt-lib",
        series_name="Reports Corrupt Series",
    )

    db.add_all([
        Comic(
            volume_id=volume.id,
            number="1",
            title="Corrupt One",
            filename="corrupt-1.cbz",
            file_path="/tmp/reports-corrupt-1.cbz",
            file_size=10,
            page_count=1,
        ),
        Comic(
            volume_id=volume.id,
            number="2",
            title="Corrupt Two",
            filename="corrupt-2.cbz",
            file_path="/tmp/reports-corrupt-2.cbz",
            file_size=20,
            page_count=2,
        ),
        Comic(
            volume_id=volume.id,
            number="3",
            title="Healthy Three",
            filename="healthy-3.cbz",
            file_path="/tmp/reports-healthy-3.cbz",
            file_size=30,
            page_count=3,
        ),
        Comic(
            volume_id=volume.id,
            number="4",
            title="Unscanned Four",
            filename="unscanned-4.cbz",
            file_path="/tmp/reports-unscanned-4.cbz",
            file_size=40,
            page_count=0,
        ),
    ])
    db.commit()

    response = admin_client.get("/api/reports/corrupt?page=1&size=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert len(payload["items"]) == 1
    assert payload["items"][0]["page_count"] in [1, 2]
