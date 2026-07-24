from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.api.home import _pick_best_cover, format_home_item
from app.models.comic import Comic, Volume
from app.models.interactions import UserComicRating, UserLibraryPin, UserVolumeFollow
from app.models.reading_progress import ReadingProgress
from app.models.series import Series
from app.models.user import User
from tests.factories import create_comic, create_library_with_root


def _create_series_graph(db, *, lib_name: str, series_name: str):
    library = create_library_with_root(db, lib_name, f"/tmp/{lib_name}")
    series = Series(name=series_name, library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()
    return library, series, volume


def _add_comic(db, volume: Volume, *, number: str, title: str, **kwargs):
    root = volume.series.library.active_root
    comic = create_comic(
        db, volume, root, f"{title.replace(' ', '-')}-{volume.id}-{number}.cbz",
        number=number,
        title=title,
        filename=f"{title.replace(' ', '-')}.cbz",
        **kwargs,
    )
    return comic


def _add_user(db, *, username: str, email: str, social_insights_enabled: bool):
    user = User(
        username=username,
        email=email,
        hashed_password="x",
        is_superuser=False,
        is_active=True,
        social_insights_enabled=social_insights_enabled,
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
    assert item["rating_mode"] == "none"
    assert item["rating_value"] is None
    assert item["rating_label"] is None
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


def test_home_top_parker_rated_orders_by_average_then_count(auth_client, db):
    _, _, volume = _create_series_graph(db, lib_name="home-parker-rated-lib", series_name="Home Parker Rated")

    top = _add_comic(db, volume, number="3", title="Parker Top")
    tiebreak = _add_comic(db, volume, number="2", title="Parker Tiebreak")
    lower = _add_comic(db, volume, number="1", title="Parker Lower")
    fourth = _add_comic(db, volume, number="4", title="Parker Fourth")

    user_a = _add_user(db, username="parker-rater-a", email="parker-rater-a@example.com", social_insights_enabled=False)
    user_b = _add_user(db, username="parker-rater-b", email="parker-rater-b@example.com", social_insights_enabled=False)
    user_c = _add_user(db, username="parker-rater-c", email="parker-rater-c@example.com", social_insights_enabled=False)
    user_d = _add_user(db, username="parker-rater-d", email="parker-rater-d@example.com", social_insights_enabled=False)

    db.add_all([
        UserComicRating(user_id=user_a.id, comic_id=top.id, rating=5),
        UserComicRating(user_id=user_b.id, comic_id=top.id, rating=5),
        UserComicRating(user_id=user_a.id, comic_id=tiebreak.id, rating=5),
        UserComicRating(user_id=user_b.id, comic_id=tiebreak.id, rating=4),
        UserComicRating(user_id=user_c.id, comic_id=tiebreak.id, rating=5),
        UserComicRating(user_id=user_a.id, comic_id=lower.id, rating=4),
        UserComicRating(user_id=user_b.id, comic_id=lower.id, rating=4),
        UserComicRating(user_id=user_c.id, comic_id=fourth.id, rating=3),
        UserComicRating(user_id=user_d.id, comic_id=fourth.id, rating=3),
    ])
    db.commit()

    response = auth_client.get("/api/home/parker-rated?limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload] == [top.id, tiebreak.id, lower.id, fourth.id]
    assert payload[0]["rating_mode"] == "parker"
    assert payload[0]["rating_value"] == 5.0
    assert payload[0]["parker_rating_average"] == 5.0
    assert payload[0]["parker_rating_count"] == 2
    assert payload[0]["rating_label"] == "Parker Rating"
    assert payload[1]["parker_rating_average"] == 14 / 3
    assert payload[1]["parker_rating_count"] == 3


def test_home_top_parker_rated_returns_empty_without_enough_qualifying_items(auth_client, db):
    _, _, volume = _create_series_graph(db, lib_name="home-parker-threshold-lib", series_name="Home Parker Threshold")

    first = _add_comic(db, volume, number="1", title="Threshold First")
    second = _add_comic(db, volume, number="2", title="Threshold Second")
    third = _add_comic(db, volume, number="3", title="Threshold Third")

    user_a = _add_user(db, username="parker-threshold-a", email="parker-threshold-a@example.com", social_insights_enabled=False)
    user_b = _add_user(db, username="parker-threshold-b", email="parker-threshold-b@example.com", social_insights_enabled=False)
    user_c = _add_user(db, username="parker-threshold-c", email="parker-threshold-c@example.com", social_insights_enabled=False)

    db.add_all([
        UserComicRating(user_id=user_a.id, comic_id=first.id, rating=5),
        UserComicRating(user_id=user_b.id, comic_id=first.id, rating=5),
        UserComicRating(user_id=user_a.id, comic_id=second.id, rating=4),
        UserComicRating(user_id=user_b.id, comic_id=second.id, rating=4),
        UserComicRating(user_id=user_c.id, comic_id=third.id, rating=5),
    ])
    db.commit()

    response = auth_client.get("/api/home/parker-rated?limit=10")

    assert response.status_code == 200
    assert response.json() == []


def test_home_top_parker_rated_applies_age_filter(auth_client, db, normal_user):
    _, _, safe_volume = _create_series_graph(db, lib_name="home-parker-age-safe-lib", series_name="Home Parker Safe")
    _, _, second_safe_volume = _create_series_graph(db, lib_name="home-parker-age-safe-lib-2", series_name="Home Parker Safe Two")
    _, _, third_safe_volume = _create_series_graph(db, lib_name="home-parker-age-safe-lib-3", series_name="Home Parker Safe Three")
    _, _, fourth_safe_volume = _create_series_graph(db, lib_name="home-parker-age-safe-lib-4", series_name="Home Parker Safe Four")
    _, _, banned_volume = _create_series_graph(db, lib_name="home-parker-age-banned-lib", series_name="Home Parker Banned")

    safe = _add_comic(db, safe_volume, number="1", title="Parker Safe", age_rating="Teen")
    safe_two = _add_comic(db, second_safe_volume, number="1", title="Parker Safe Two", age_rating="Teen")
    safe_three = _add_comic(db, third_safe_volume, number="1", title="Parker Safe Three", age_rating="Teen")
    safe_four = _add_comic(db, fourth_safe_volume, number="1", title="Parker Safe Four", age_rating="Teen")
    banned = _add_comic(db, banned_volume, number="1", title="Parker Banned", age_rating="Mature 17+")

    rater = _add_user(db, username="parker-age-rater", email="parker-age-rater@example.com", social_insights_enabled=False)
    db.add_all([
        UserComicRating(user_id=rater.id, comic_id=safe.id, rating=4),
        UserComicRating(user_id=normal_user.id, comic_id=safe.id, rating=4),
        UserComicRating(user_id=rater.id, comic_id=safe_two.id, rating=4),
        UserComicRating(user_id=normal_user.id, comic_id=safe_two.id, rating=4),
        UserComicRating(user_id=rater.id, comic_id=safe_three.id, rating=4),
        UserComicRating(user_id=normal_user.id, comic_id=safe_three.id, rating=4),
        UserComicRating(user_id=rater.id, comic_id=safe_four.id, rating=4),
        UserComicRating(user_id=normal_user.id, comic_id=safe_four.id, rating=4),
        UserComicRating(user_id=normal_user.id, comic_id=banned.id, rating=5),
        UserComicRating(user_id=rater.id, comic_id=banned.id, rating=5),
    ])

    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    response = auth_client.get("/api/home/parker-rated?limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 4
    assert {item["id"] for item in payload} == {safe.id, safe_two.id, safe_three.id, safe_four.id}


def test_home_trending_returns_empty_below_threshold(auth_client, db):
    sharer = _add_user(
        db,
        username="trending-threshold-sharer",
        email="trending-threshold-sharer@example.com",
        social_insights_enabled=True,
    )

    now = datetime.now(timezone.utc)
    comics = []
    for idx in range(1, 4):
        _, _, volume = _create_series_graph(
            db,
            lib_name=f"home-trending-threshold-lib-{idx}",
            series_name=f"Home Trending Threshold {idx}",
        )
        comics.append(_add_comic(db, volume, number="1", title=f"Trending Threshold #{idx}"))

    for comic in comics:
        db.add(
            ReadingProgress(
                user_id=sharer.id,
                comic_id=comic.id,
                current_page=5,
                total_pages=10,
                completed=False,
                last_read_at=now - timedelta(days=2),
            )
        )

    db.commit()

    response = auth_client.get("/api/home/trending?limit=10")

    assert response.status_code == 200
    assert response.json() == []


def test_home_trending_orders_by_recent_activity_then_readers_then_latest(auth_client, db, normal_user):
    now = datetime.now(timezone.utc)

    sharer_a = _add_user(
        db,
        username="trending-sharer-a",
        email="trending-sharer-a@example.com",
        social_insights_enabled=True,
    )
    sharer_b = _add_user(
        db,
        username="trending-sharer-b",
        email="trending-sharer-b@example.com",
        social_insights_enabled=True,
    )
    sharer_c = _add_user(
        db,
        username="trending-sharer-c",
        email="trending-sharer-c@example.com",
        social_insights_enabled=True,
    )
    hidden = _add_user(
        db,
        username="trending-hidden",
        email="trending-hidden@example.com",
        social_insights_enabled=False,
    )

    _, top_series, top_volume = _create_series_graph(db, lib_name="home-trending-top-lib", series_name="Trending Top")
    top_comics = [
        _add_comic(db, top_volume, number="1", title="Trending Top #1", year=2024, publisher="Trending Pub"),
        _add_comic(db, top_volume, number="2", title="Trending Top #2", year=2024, publisher="Trending Pub"),
        _add_comic(db, top_volume, number="3", title="Trending Top #3", year=2024, publisher="Trending Pub"),
    ]

    _, tiebreak_series, tiebreak_volume = _create_series_graph(db, lib_name="home-trending-tiebreak-lib", series_name="Trending Tiebreak")
    tiebreak_comics = [
        _add_comic(db, tiebreak_volume, number="1", title="Trending Tiebreak #1", year=2024, publisher="Trending Pub"),
        _add_comic(db, tiebreak_volume, number="2", title="Trending Tiebreak #2", year=2024, publisher="Trending Pub"),
        _add_comic(db, tiebreak_volume, number="3", title="Trending Tiebreak #3", year=2024, publisher="Trending Pub"),
    ]

    _, fresh_series, fresh_volume = _create_series_graph(db, lib_name="home-trending-fresh-lib", series_name="Trending Fresh")
    fresh_comics = [
        _add_comic(db, fresh_volume, number="1", title="Trending Fresh #1", year=2024, publisher="Trending Pub"),
        _add_comic(db, fresh_volume, number="2", title="Trending Fresh #2", year=2024, publisher="Trending Pub"),
    ]

    _, minimal_series, minimal_volume = _create_series_graph(db, lib_name="home-trending-minimal-lib", series_name="Trending Minimal")
    minimal_comic = _add_comic(db, minimal_volume, number="1", title="Trending Minimal #1", year=2024, publisher="Trending Pub")

    _, stale_series, stale_volume = _create_series_graph(db, lib_name="home-trending-stale-lib", series_name="Trending Stale")
    stale_comics = [
        _add_comic(db, stale_volume, number="1", title="Trending Stale #1", year=2024, publisher="Trending Pub"),
        _add_comic(db, stale_volume, number="2", title="Trending Stale #2", year=2024, publisher="Trending Pub"),
        _add_comic(db, stale_volume, number="3", title="Trending Stale #3", year=2024, publisher="Trending Pub"),
    ]

    db.add_all([
        ReadingProgress(user_id=sharer_a.id, comic_id=top_comics[0].id, current_page=6, total_pages=10, completed=False, last_read_at=now - timedelta(hours=3)),
        ReadingProgress(user_id=sharer_b.id, comic_id=top_comics[1].id, current_page=10, total_pages=10, completed=True, last_read_at=now - timedelta(hours=2)),
        ReadingProgress(user_id=sharer_a.id, comic_id=top_comics[2].id, current_page=8, total_pages=10, completed=False, last_read_at=now - timedelta(hours=1)),

        ReadingProgress(user_id=sharer_c.id, comic_id=tiebreak_comics[0].id, current_page=4, total_pages=10, completed=False, last_read_at=now - timedelta(minutes=20)),
        ReadingProgress(user_id=sharer_c.id, comic_id=tiebreak_comics[1].id, current_page=5, total_pages=10, completed=False, last_read_at=now - timedelta(minutes=10)),
        ReadingProgress(user_id=sharer_c.id, comic_id=tiebreak_comics[2].id, current_page=10, total_pages=10, completed=True, last_read_at=now - timedelta(minutes=5)),

        ReadingProgress(user_id=sharer_a.id, comic_id=fresh_comics[0].id, current_page=6, total_pages=10, completed=False, last_read_at=now - timedelta(minutes=3)),
        ReadingProgress(user_id=sharer_b.id, comic_id=fresh_comics[1].id, current_page=10, total_pages=10, completed=True, last_read_at=now - timedelta(minutes=2)),

        ReadingProgress(user_id=sharer_b.id, comic_id=minimal_comic.id, current_page=5, total_pages=10, completed=False, last_read_at=now - timedelta(minutes=1)),

        ReadingProgress(user_id=sharer_a.id, comic_id=stale_comics[0].id, current_page=5, total_pages=10, completed=False, last_read_at=now - timedelta(days=45)),
        ReadingProgress(user_id=sharer_b.id, comic_id=stale_comics[1].id, current_page=5, total_pages=10, completed=False, last_read_at=now - timedelta(days=44)),
        ReadingProgress(user_id=sharer_c.id, comic_id=stale_comics[2].id, current_page=5, total_pages=10, completed=False, last_read_at=now - timedelta(days=43)),
        ReadingProgress(user_id=hidden.id, comic_id=top_comics[0].id, current_page=10, total_pages=10, completed=True, last_read_at=now),
        ReadingProgress(user_id=normal_user.id, comic_id=fresh_comics[0].id, current_page=9, total_pages=10, completed=False, last_read_at=now),
    ])
    db.commit()

    response = auth_client.get("/api/home/trending?limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload] == [
        top_series.id,
        tiebreak_series.id,
        fresh_series.id,
        minimal_series.id,
    ]
    assert stale_series.id not in [item["id"] for item in payload]


def test_home_trending_applies_age_filter(auth_client, db, normal_user):
    now = datetime.now(timezone.utc)
    sharer_a = _add_user(
        db,
        username="trending-age-a",
        email="trending-age-a@example.com",
        social_insights_enabled=True,
    )
    sharer_b = _add_user(
        db,
        username="trending-age-b",
        email="trending-age-b@example.com",
        social_insights_enabled=True,
    )

    safe_ids = set()
    safe_comics = []
    for idx in range(1, 5):
        _, series, volume = _create_series_graph(
            db,
            lib_name=f"home-trending-age-safe-lib-{idx}",
            series_name=f"Home Trending Age Safe {idx}",
        )
        comic = _add_comic(
            db,
            volume,
            number="1",
            title=f"Trending Age Safe #{idx}",
            age_rating="Teen",
            year=2024,
            publisher="Trending Pub",
        )
        safe_ids.add(series.id)
        safe_comics.append(comic)

    _, banned_series, banned_volume = _create_series_graph(
        db,
        lib_name="home-trending-age-banned-lib",
        series_name="Home Trending Age Banned",
    )
    banned_comics = [
        _add_comic(db, banned_volume, number="1", title="Trending Age Banned #1", age_rating="Mature 17+", year=2024, publisher="Trending Pub"),
        _add_comic(db, banned_volume, number="2", title="Trending Age Banned #2", age_rating="Mature 17+", year=2024, publisher="Trending Pub"),
    ]

    db.add_all([
        ReadingProgress(user_id=sharer_a.id, comic_id=safe_comics[0].id, current_page=6, total_pages=10, completed=False, last_read_at=now - timedelta(days=1)),
        ReadingProgress(user_id=sharer_b.id, comic_id=safe_comics[0].id, current_page=10, total_pages=10, completed=True, last_read_at=now - timedelta(hours=20)),
        ReadingProgress(user_id=sharer_a.id, comic_id=safe_comics[1].id, current_page=6, total_pages=10, completed=False, last_read_at=now - timedelta(days=2)),
        ReadingProgress(user_id=sharer_b.id, comic_id=safe_comics[1].id, current_page=10, total_pages=10, completed=True, last_read_at=now - timedelta(days=2, hours=1)),
        ReadingProgress(user_id=sharer_a.id, comic_id=safe_comics[2].id, current_page=6, total_pages=10, completed=False, last_read_at=now - timedelta(days=3)),
        ReadingProgress(user_id=sharer_b.id, comic_id=safe_comics[2].id, current_page=10, total_pages=10, completed=True, last_read_at=now - timedelta(days=3, hours=1)),
        ReadingProgress(user_id=sharer_a.id, comic_id=safe_comics[3].id, current_page=6, total_pages=10, completed=False, last_read_at=now - timedelta(days=4)),
        ReadingProgress(user_id=sharer_b.id, comic_id=safe_comics[3].id, current_page=10, total_pages=10, completed=True, last_read_at=now - timedelta(days=4, hours=1)),
        ReadingProgress(user_id=sharer_a.id, comic_id=banned_comics[0].id, current_page=8, total_pages=10, completed=False, last_read_at=now - timedelta(minutes=2)),
        ReadingProgress(user_id=sharer_b.id, comic_id=banned_comics[1].id, current_page=10, total_pages=10, completed=True, last_read_at=now - timedelta(minutes=1)),
    ])

    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    response = auth_client.get("/api/home/trending?limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 4
    assert {item["id"] for item in payload} == safe_ids
    assert banned_series.id not in {item["id"] for item in payload}


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


def test_home_following_arrivals_respects_baseline_formats_and_progress(auth_client, db, normal_user):
    library, _, volume = _create_series_graph(
        db,
        lib_name="home-follow-lib",
        series_name="Home Follow",
    )
    _, _, second_volume = _create_series_graph(
        db,
        lib_name="home-follow-lib-two",
        series_name="Home Follow Two",
    )

    normal_user.accessible_libraries.extend([library, second_volume.series.library])
    baseline = datetime(2026, 7, 1, tzinfo=timezone.utc)

    old_plain = _add_comic(
        db,
        volume,
        number="1",
        title="Follow Old Plain",
        created_at=baseline - timedelta(days=1),
        format=None,
    )
    new_plain = _add_comic(
        db,
        volume,
        number="2",
        title="Follow New Plain",
        created_at=baseline + timedelta(hours=1),
        format=None,
    )
    annual = _add_comic(
        db,
        volume,
        number="1",
        title="Follow Annual",
        created_at=baseline + timedelta(hours=2),
        format="annual",
    )
    started_plain = _add_comic(
        db,
        volume,
        number="3",
        title="Follow Started Plain",
        created_at=baseline + timedelta(hours=3),
        format=None,
    )
    completed_plain = _add_comic(
        db,
        volume,
        number="4",
        title="Follow Completed Plain",
        created_at=baseline + timedelta(hours=4),
        format=None,
    )
    newest_plain = _add_comic(
        db,
        second_volume,
        number="5",
        title="Follow Newest Plain",
        created_at=baseline + timedelta(hours=5),
        format=None,
    )

    db.add_all([
        UserVolumeFollow(user_id=normal_user.id, volume_id=volume.id, followed_at=baseline),
        UserVolumeFollow(user_id=normal_user.id, volume_id=second_volume.id, followed_at=baseline),
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=started_plain.id,
            current_page=3,
            total_pages=20,
            completed=False,
        ),
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=completed_plain.id,
            current_page=20,
            total_pages=20,
            completed=True,
        ),
    ])
    db.commit()

    response = auth_client.get("/api/home/following-arrivals?limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload] == [newest_plain.id, new_plain.id]
    assert old_plain.id not in [item["id"] for item in payload]
    assert annual.id not in [item["id"] for item in payload]
    assert started_plain.id not in [item["id"] for item in payload]
    assert completed_plain.id not in [item["id"] for item in payload]


def test_home_pinned_libraries_returns_recently_updated_series_by_pin_order(auth_client, db, normal_user):
    first_library = create_library_with_root(db, "Pinned First", "/tmp/pinned-first")
    second_library = create_library_with_root(db, "Pinned Second", "/tmp/pinned-second")
    empty_library = create_library_with_root(db, "Pinned Empty", "/tmp/pinned-empty")
    hidden_library = create_library_with_root(db, "Pinned Hidden", "/tmp/pinned-hidden")

    normal_user.accessible_libraries.extend([first_library, second_library, empty_library])
    pinned_at = datetime(2026, 7, 20, tzinfo=timezone.utc)
    db.add_all([
        UserLibraryPin(user_id=normal_user.id, library_id=first_library.id, pinned_at=pinned_at),
        UserLibraryPin(user_id=normal_user.id, library_id=second_library.id, pinned_at=pinned_at + timedelta(minutes=1)),
        UserLibraryPin(user_id=normal_user.id, library_id=empty_library.id, pinned_at=pinned_at + timedelta(minutes=2)),
        UserLibraryPin(user_id=normal_user.id, library_id=hidden_library.id, pinned_at=pinned_at + timedelta(minutes=3)),
    ])

    for index in range(16):
        series = Series(name=f"First Updated {index:02d}", library=first_library)
        volume = Volume(series=series, volume_number=1)
        db.add_all([series, volume])
        db.flush()
        _add_comic(
            db,
            volume,
            number="1",
            title=f"First Updated Comic {index:02d}",
            year=2020 + index,
            updated_at=pinned_at + timedelta(hours=index),
        )

    older_second = Series(name="Second Older", library=second_library)
    newer_second = Series(name="Second Newer", library=second_library)
    older_volume = Volume(series=older_second, volume_number=1)
    newer_volume = Volume(series=newer_second, volume_number=1)
    hidden_series = Series(name="Hidden Series", library=hidden_library)
    hidden_volume = Volume(series=hidden_series, volume_number=1)
    db.add_all([older_second, newer_second, older_volume, newer_volume, hidden_series, hidden_volume])
    db.flush()
    _add_comic(db, older_volume, number="1", title="Second Older Comic", updated_at=pinned_at)
    _add_comic(db, newer_volume, number="1", title="Second Newer Comic", updated_at=pinned_at + timedelta(days=1))
    _add_comic(db, hidden_volume, number="1", title="Hidden Comic", updated_at=pinned_at + timedelta(days=2))
    db.commit()

    response = auth_client.get("/api/home/pinned-libraries?limit=99")

    assert response.status_code == 200
    payload = response.json()
    assert [rail["name"] for rail in payload] == ["Pinned First", "Pinned Second"]
    assert len(payload[0]["items"]) == 15
    assert payload[0]["items"][0]["name"] == "First Updated 15"
    assert payload[0]["items"][-1]["name"] == "First Updated 01"
    assert [item["name"] for item in payload[1]["items"]] == ["Second Newer", "Second Older"]
    assert "Pinned Empty" not in [rail["name"] for rail in payload]
    assert "Pinned Hidden" not in [rail["name"] for rail in payload]


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
        social_insights_enabled=True,
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
        social_insights_enabled=True,
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
        social_insights_enabled=True,
    )
    sharer_b = _add_user(
        db,
        username="popular-full-sharer-b",
        email="popular-full-sharer-b@example.com",
        social_insights_enabled=True,
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
