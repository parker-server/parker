from app.core.security import get_password_hash
from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.reading_progress import ReadingProgress
from app.models.series import Series
from app.models.tags import Genre
from app.models.user import User


def _create_series_volume(db, *, prefix: str, series_suffix: str):
    library = Library(name=f"{prefix}-lib", path=f"/tmp/{prefix}-lib")
    series = Series(name=f"{prefix}-series-{series_suffix}", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([library, series, volume])
    db.flush()
    return library, series, volume


def test_stats_endpoints_require_admin(auth_client):
    system = auth_client.get("/api/stats/")
    genres = auth_client.get("/api/stats/genres")

    assert system.status_code == 400
    assert genres.status_code == 400
    assert "privileges" in system.json()["detail"].lower()
    assert "privileges" in genres.json()["detail"].lower()


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


def test_system_stats_aggregates_counts_storage_and_activity(admin_client, db, admin_user):
    lib = Library(name="stats-lib", path="/tmp/stats-lib")
    series_a = Series(name="stats-series-a", library=lib)
    series_b = Series(name="stats-series-b", library=lib)
    vol_a = Volume(series=series_a, volume_number=1)
    vol_b = Volume(series=series_b, volume_number=1)
    db.add_all([lib, series_a, series_b, vol_a, vol_b])
    db.flush()

    c1 = Comic(volume_id=vol_a.id, number="1", filename="stats-1.cbz", file_path="/tmp/stats-1.cbz", file_size=100, page_count=10)
    c2 = Comic(volume_id=vol_a.id, number="2", filename="stats-2.cbz", file_path="/tmp/stats-2.cbz", file_size=200, page_count=12)
    c3 = Comic(volume_id=vol_b.id, number="1", filename="stats-3.cbz", file_path="/tmp/stats-3.cbz", file_size=300, page_count=14)
    db.add_all([c1, c2, c3])
    db.flush()

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
    lib = Library(name="stats-genre-lib", path="/tmp/stats-genre-lib")
    series = Series(name="stats-genre-series", library=lib)
    volume = Volume(series=series, volume_number=1)
    db.add_all([lib, series, volume])
    db.flush()

    c1 = Comic(volume_id=volume.id, number="1", filename="genre-1.cbz", file_path="/tmp/genre-1.cbz", file_size=100, page_count=10)
    c2 = Comic(volume_id=volume.id, number="2", filename="genre-2.cbz", file_path="/tmp/genre-2.cbz", file_size=250, page_count=12)
    c3 = Comic(volume_id=volume.id, number="3", filename="genre-3.cbz", file_path="/tmp/genre-3.cbz", file_size=400, page_count=14)

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
