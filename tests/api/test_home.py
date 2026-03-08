from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.api.home import _pick_best_cover, format_home_item
from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.reading_progress import ReadingProgress
from app.models.series import Series
from app.models.user import User


def _create_series_graph(db, *, lib_name: str, series_name: str):
    library = Library(name=lib_name, path=f"/tmp/{lib_name}")
    series = Series(name=series_name, library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([library, series, volume])
    db.flush()
    return library, series, volume


def _add_comic(db, volume: Volume, *, number: str, title: str, **kwargs):
    comic = Comic(
        volume_id=volume.id,
        number=number,
        title=title,
        filename=f"{title.replace(' ', '-')}.cbz",
        file_path=f"/tmp/{title.replace(' ', '-')}-{volume.id}-{number}.cbz",
        **kwargs,
    )
    db.add(comic)
    db.flush()
    return comic


def _add_user(db, *, username: str, email: str, share_progress_enabled: bool):
    user = User(
        username=username,
        email=email,
        hashed_password="x",
        is_superuser=False,
        is_active=True,
        share_progress_enabled=share_progress_enabled,
    )
    db.add(user)
    db.flush()
    return user


def test_format_home_item_and_pick_best_cover_helpers():
    comic = Comic(
        id=11,
        number="7",
        title="Helper Comic",
        year=2025,
        publisher="Helper Pub",
        page_count=10,
        updated_at=None,
    )
    progress = ReadingProgress(user_id=1, comic_id=11, current_page=15, total_pages=10, completed=False)

    item = format_home_item(comic, progress)
    assert item["id"] == 11
    assert item["series"] == "Unknown"
    assert item["volume"] == 0
    assert item["thumbnail_path"] == "/api/comics/11/thumbnail?v=0"
    assert item["progress_percentage"] == 100.0

    no_progress = format_home_item(comic)
    assert no_progress["progress_percentage"] is None

    assert _pick_best_cover(SimpleNamespace(name="Any"), []) is None

    non_reverse = SimpleNamespace(name="Regular")
    regular_pool = [
        SimpleNamespace(number="A", format=None),
        SimpleNamespace(number="3", format=None),
        SimpleNamespace(number="2", format="annual"),
    ]
    picked_regular = _pick_best_cover(non_reverse, regular_pool)
    assert picked_regular.number == "3"

    issue_one_pool = [
        SimpleNamespace(number="2", format=None),
        SimpleNamespace(number="1", format=None),
    ]
    picked_issue_one = _pick_best_cover(non_reverse, issue_one_pool)
    assert picked_issue_one.number == "1"

    reverse = SimpleNamespace(name="Countdown")
    reverse_pool = [
        SimpleNamespace(number="1", format=None),
        SimpleNamespace(number="4", format=None),
    ]
    picked_reverse = _pick_best_cover(reverse, reverse_pool)
    assert picked_reverse.number == "4"


def test_home_random_empty_and_skips_series_without_comics(auth_client, db):
    empty = auth_client.get("/api/home/random?limit=10")
    assert empty.status_code == 200
    assert empty.json() == []

    _, no_comics_series, _ = _create_series_graph(
        db,
        lib_name="home-random-empty-lib",
        series_name="Home Random Empty",
    )
    _, full_series, full_volume = _create_series_graph(
        db,
        lib_name="home-random-full-lib",
        series_name="Home Random Full",
    )
    first = _add_comic(
        db,
        full_volume,
        number="1",
        title="Home Random #1",
        year=2022,
        publisher="Random Pub",
    )
    db.commit()

    response = auth_client.get("/api/home/random?limit=10")
    assert response.status_code == 200
    payload = response.json()

    assert len(payload) == 1
    assert payload[0]["id"] == full_series.id
    assert payload[0]["name"] == "Home Random Full"
    assert payload[0]["volume_count"] == 1
    assert payload[0]["publisher"] == "Random Pub"
    assert payload[0]["thumbnail_path"].startswith(f"/api/comics/{first.id}/thumbnail?v=")
    assert no_comics_series.id not in [item["id"] for item in payload]


def test_home_rated_orders_by_rating(auth_client, db):
    _, _, volume = _create_series_graph(db, lib_name="home-rated-lib", series_name="Home Rated")

    high = _add_comic(
        db,
        volume,
        number="2",
        title="Top Rated",
        community_rating=4.9,
    )
    _add_comic(
        db,
        volume,
        number="1",
        title="Lower Rated",
        community_rating=4.1,
    )
    _add_comic(
        db,
        volume,
        number="0",
        title="Below Threshold",
        community_rating=3.9,
    )
    db.commit()

    response = auth_client.get("/api/home/rated?limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert payload[0]["id"] == high.id
    assert payload[0]["community_rating"] == 4.9


def test_home_resume_applies_staleness_and_progress_percentage(auth_client, db, normal_user):
    _, _, volume = _create_series_graph(db, lib_name="home-resume-lib", series_name="Home Resume")

    recent = _add_comic(db, volume, number="1", title="Recent Resume", page_count=10)
    stale = _add_comic(db, volume, number="2", title="Stale Resume", page_count=20)

    db.add_all([
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=recent.id,
            current_page=15,
            total_pages=10,
            completed=False,
            last_read_at=datetime.now(timezone.utc) - timedelta(days=2),
        ),
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=stale.id,
            current_page=5,
            total_pages=20,
            completed=False,
            last_read_at=datetime.now(timezone.utc) - timedelta(weeks=8),
        ),
    ])
    db.commit()

    with patch("app.api.home.get_cached_setting", return_value=1):
        response = auth_client.get("/api/home/resume?limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["id"] == recent.id
    assert payload[0]["progress_percentage"] == 100.0


def test_home_up_next_handles_duplicates_reverse_and_non_numeric(auth_client, db, normal_user):
    _, _, standard_volume = _create_series_graph(
        db,
        lib_name="home-up-next-standard-lib",
        series_name="Up Next Standard",
    )
    std1 = _add_comic(db, standard_volume, number="1", title="Std #1")
    std2 = _add_comic(db, standard_volume, number="2", title="Std #2")
    std3 = _add_comic(db, standard_volume, number="3", title="Std #3")

    _, _, bad_volume = _create_series_graph(
        db,
        lib_name="home-up-next-bad-lib",
        series_name="Up Next Bad",
    )
    bad = _add_comic(db, bad_volume, number="A", title="Bad #A")

    _, _, reverse_volume = _create_series_graph(
        db,
        lib_name="home-up-next-reverse-lib",
        series_name="Countdown",
    )
    rev3 = _add_comic(db, reverse_volume, number="3", title="Rev #3")
    rev2 = _add_comic(db, reverse_volume, number="2", title="Rev #2")
    _add_comic(db, reverse_volume, number="1", title="Rev #1")

    now = datetime.now(timezone.utc)
    db.add_all([
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=std2.id,
            current_page=22,
            total_pages=22,
            completed=True,
            last_read_at=now,
        ),
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=bad.id,
            current_page=10,
            total_pages=10,
            completed=True,
            last_read_at=now - timedelta(minutes=1),
        ),
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=std1.id,
            current_page=20,
            total_pages=20,
            completed=True,
            last_read_at=now - timedelta(minutes=2),
        ),
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=rev3.id,
            current_page=24,
            total_pages=24,
            completed=True,
            last_read_at=now - timedelta(minutes=3),
        ),
    ])
    db.commit()

    with patch("app.api.home.get_cached_setting", return_value=0):
        response = auth_client.get("/api/home/up-next?limit=2")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert [item["id"] for item in payload] == [std3.id, rev2.id]


def test_home_popular_returns_empty_below_threshold(auth_client, db, normal_user):
    sharer = _add_user(
        db,
        username="popular-threshold-sharer",
        email="popular-threshold-sharer@example.com",
        share_progress_enabled=True,
    )

    comics = []
    for idx in range(1, 4):
        _, _, volume = _create_series_graph(
            db,
            lib_name=f"home-popular-threshold-lib-{idx}",
            series_name=f"Home Popular Threshold {idx}",
        )
        comics.append(_add_comic(db, volume, number="1", title=f"Threshold #{idx}"))

    for comic in comics:
        db.add(
            ReadingProgress(
                user_id=sharer.id,
                comic_id=comic.id,
                current_page=5,
                total_pages=10,
                completed=True,
            )
        )

    db.commit()

    response = auth_client.get("/api/home/popular?limit=10")

    assert response.status_code == 200
    assert response.json() == []


def test_home_popular_secondary_guard_when_cover_picker_drops_items(auth_client, db):
    sharer = _add_user(
        db,
        username="popular-cover-sharer",
        email="popular-cover-sharer@example.com",
        share_progress_enabled=True,
    )

    for idx in range(1, 5):
        _, _, volume = _create_series_graph(
            db,
            lib_name=f"home-popular-cover-lib-{idx}",
            series_name=f"Home Popular Cover {idx}",
        )
        comic = _add_comic(db, volume, number="1", title=f"Cover #{idx}")
        db.add(
            ReadingProgress(
                user_id=sharer.id,
                comic_id=comic.id,
                current_page=5,
                total_pages=10,
                completed=True,
            )
        )

    db.commit()

    with patch("app.api.home._pick_best_cover", side_effect=lambda series_obj, comics_list: None if series_obj.name.endswith("4") else comics_list[0]):
        response = auth_client.get("/api/home/popular?limit=10")

    assert response.status_code == 200
    assert response.json() == []


def test_home_popular_returns_series_when_enough_data(auth_client, db):
    sharer_a = _add_user(
        db,
        username="popular-full-sharer-a",
        email="popular-full-sharer-a@example.com",
        share_progress_enabled=True,
    )
    sharer_b = _add_user(
        db,
        username="popular-full-sharer-b",
        email="popular-full-sharer-b@example.com",
        share_progress_enabled=True,
    )

    series_ids = []
    for idx in range(1, 5):
        _, series, volume = _create_series_graph(
            db,
            lib_name=f"home-popular-full-lib-{idx}",
            series_name=f"Home Popular Full {idx}",
        )
        comic = _add_comic(db, volume, number="1", title=f"Full #{idx}", year=2020 + idx, publisher="Popular Pub")
        series_ids.append(series.id)
        db.add_all([
            ReadingProgress(
                user_id=sharer_a.id,
                comic_id=comic.id,
                current_page=5,
                total_pages=10,
                completed=True,
            ),
            ReadingProgress(
                user_id=sharer_b.id,
                comic_id=comic.id,
                current_page=3,
                total_pages=10,
                completed=False,
            ),
        ])

    db.commit()

    response = auth_client.get("/api/home/popular?limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 4
    assert {row["id"] for row in payload} == set(series_ids)
    assert all(row["publisher"] == "Popular Pub" for row in payload)
