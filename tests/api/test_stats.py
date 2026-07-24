from app.core.security import get_password_hash
from app.models.comic import Volume
from app.models.reading_progress import ReadingProgress
from app.models.series import Series
from app.models.tags import Genre
from app.models.user import User
from tests.factories import create_comic, create_library_with_root


def _create_series_volume(db, *, prefix: str, series_suffix: str):
    library = create_library_with_root(db, f"{prefix}-lib", f"/tmp/{prefix}-lib")
    series = Series(name=f"{prefix}-series-{series_suffix}", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()
    return library, series, volume


def test_stats_endpoints_require_admin(auth_client):
    system = auth_client.get("/api/stats/")
    genres = auth_client.get("/api/stats/genres")
    startup = auth_client.get("/api/stats/startup")

    assert system.status_code == 400
    assert genres.status_code == 400
    assert startup.status_code == 400
    assert "privileges" in system.json()["detail"].lower()
    assert "privileges" in genres.json()["detail"].lower()
    assert "privileges" in startup.json()["detail"].lower()


def test_system_stats_empty_defaults(admin_client):
    response = admin_client.get("/api/stats/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["counts"] == {
        "libraries": 0,
        "series": 0,
        "volumes": 0,
        "comics": 0,
        "users": 1,
    }
    assert payload["storage"]["total_bytes"] == 0
    assert payload["activity"] == {"pages_read": 0, "completed_books": 0}


def test_startup_diagnostics_endpoint_returns_collected_payload(admin_client, monkeypatch):
    sentinel = {
        "status": "storage_mismatch_suspected",
        "status_title": "Storage Mismatch Suspected",
        "status_summary": "Mismatch summary",
        "is_suspicious": True,
        "database": {"path": "/app/storage/database/comics.db"},
        "counts": {"users": 1, "libraries": 0, "series": 0, "comics": 0},
        "default_admin_present": True,
        "library_sample": [],
        "comics_root": {"path": "/comics", "exists": True, "sample": ["Marvel/"]},
        "recommended_actions": ["Check storage"],
    }

    monkeypatch.setattr("app.api.stats.collect_startup_diagnostics", lambda db, database_url: sentinel)

    response = admin_client.get("/api/stats/startup")

    assert response.status_code == 200
    assert response.json() == sentinel


def test_startup_support_snapshot_endpoint_returns_structured_snapshot(admin_client, monkeypatch):
    sentinel = {"status": "healthy"}
    snapshot = {"snapshot_type": "parker_startup_diagnostics", "schema_version": 1}

    monkeypatch.setattr("app.api.stats.collect_startup_diagnostics", lambda db, database_url: sentinel)
    monkeypatch.setattr("app.api.stats.build_support_snapshot", lambda diagnostics, app_version: snapshot)

    response = admin_client.get("/api/stats/startup/support-snapshot")

    assert response.status_code == 200
    assert response.json() == snapshot


def test_system_stats_aggregates_counts_storage_and_activity(admin_client, db, admin_user):
    lib = create_library_with_root(db, "stats-lib", "/tmp/stats-lib")
    root = lib.active_root
    series_a = Series(name="stats-series-a", library=lib)
    series_b = Series(name="stats-series-b", library=lib)
    vol_a = Volume(series=series_a, volume_number=1)
    vol_b = Volume(series=series_b, volume_number=1)
    db.add_all([series_a, series_b, vol_a, vol_b])
    db.flush()

    c1 = create_comic(db, vol_a, root, "stats-1.cbz", number="1", filename="stats-1.cbz", file_size=100, page_count=10)
    c2 = create_comic(db, vol_a, root, "stats-2.cbz", number="2", filename="stats-2.cbz", file_size=200, page_count=12)
    c3 = create_comic(db, vol_b, root, "stats-3.cbz", number="1", filename="stats-3.cbz", file_size=300, page_count=14)

    other_user = User(
        username="stats-other",
        email="stats-other@example.com",
        hashed_password=get_password_hash("test1234"),
        is_superuser=False,
        is_active=True,
    )
    db.add(other_user)
    db.flush()

    db.add_all([
        ReadingProgress(user_id=admin_user.id, comic_id=c1.id, current_page=5, total_pages=10, completed=True),
        ReadingProgress(user_id=admin_user.id, comic_id=c2.id, current_page=2, total_pages=12, completed=False),
        ReadingProgress(user_id=other_user.id, comic_id=c3.id, current_page=8, total_pages=14, completed=True),
    ])
    db.commit()

    response = admin_client.get("/api/stats/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["counts"] == {
        "libraries": 1,
        "series": 2,
        "volumes": 2,
        "comics": 3,
        "users": 2,
    }
    assert payload["storage"]["total_bytes"] == 600
    assert payload["activity"] == {"pages_read": 15, "completed_books": 2}


def test_genre_stats_aggregate_inventory_read_percent_and_size(admin_client, db, admin_user):
    lib = create_library_with_root(db, "stats-genre-lib", "/tmp/stats-genre-lib")
    root = lib.active_root
    series = Series(name="stats-genre-series", library=lib)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()

    c1 = create_comic(db, volume, root, "genre-1.cbz", number="1", filename="genre-1.cbz", file_size=100, page_count=10)
    c2 = create_comic(db, volume, root, "genre-2.cbz", number="2", filename="genre-2.cbz", file_size=250, page_count=12)
    c3 = create_comic(db, volume, root, "genre-3.cbz", number="3", filename="genre-3.cbz", file_size=400, page_count=14)

    action = Genre(name="Action")
    sci_fi = Genre(name="Sci-Fi")

    c1.genres.append(action)
    c2.genres.extend([action, sci_fi])
    c3.genres.append(sci_fi)

    other_user = User(
        username="genre-other",
        email="genre-other@example.com",
        hashed_password=get_password_hash("test1234"),
        is_superuser=False,
        is_active=True,
    )

    db.add_all([c1, c2, c3, action, sci_fi, other_user])
    db.flush()

    db.add_all([
        ReadingProgress(user_id=admin_user.id, comic_id=c1.id, current_page=9, total_pages=10, completed=True),
        ReadingProgress(user_id=admin_user.id, comic_id=c2.id, current_page=2, total_pages=12, completed=False),
        ReadingProgress(user_id=other_user.id, comic_id=c2.id, current_page=11, total_pages=12, completed=True),
        ReadingProgress(user_id=other_user.id, comic_id=c3.id, current_page=13, total_pages=14, completed=True),
    ])
    db.commit()

    response = admin_client.get("/api/stats/genres")

    assert response.status_code == 200
    rows = {item["genre"]: item for item in response.json()}

    assert rows["Action"] == {
        "genre": "Action",
        "inventory": 2,
        "read_count": 1,
        "read_pct": 50.0,
        "size_bytes": 350,
    }
    assert rows["Sci-Fi"] == {
        "genre": "Sci-Fi",
        "inventory": 2,
        "read_count": 0,
        "read_pct": 0,
        "size_bytes": 650,
    }
