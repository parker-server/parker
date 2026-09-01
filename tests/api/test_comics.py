from pathlib import Path
from unittest.mock import patch

from app.api.deps import get_current_user
from app.api.comics import filter_by_user_access, natural_sort_key
from app.main import app
from app.models.collection import Collection, CollectionItem
from app.models.bookmark import Bookmark
from app.models.comic import Comic, Volume
from app.models.credits import ComicCredit, Person
from app.models.interactions import UserComicRating
from app.models.pull_list import PullList, PullListItem
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.reading_progress import ReadingProgress
from app.models.series import Series
from app.models.tags import Character, Genre, Location, Team
from app.models.user import User
from tests.factories import create_comic, create_library_with_root


def _create_graph(db, *, lib_name: str, series_name: str):
    library = create_library_with_root(db, lib_name, f"/tmp/{lib_name}")
    series = Series(name=series_name, library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()
    return library, series, volume


def test_filter_by_user_access_and_natural_sort_key(db, admin_user, normal_user):
    lib_a, _, vol_a = _create_graph(db, lib_name="comic-access-a", series_name="Access A")
    lib_b, _, vol_b = _create_graph(db, lib_name="comic-access-b", series_name="Access B")

    comic_a = create_comic(
        db, vol_a, lib_a.active_root, "a-1.cbz",
        number="1",
        title="A #1",
        filename="a-1.cbz",
    )
    comic_b = create_comic(
        db, vol_b, lib_b.active_root, "b-1.cbz",
        number="1",
        title="B #1",
        filename="b-1.cbz",
    )

    normal_user.accessible_libraries.append(lib_a)
    db.commit()

    base = db.query(Comic).join(Volume).join(Series)

    admin_visible = filter_by_user_access(base, admin_user).all()
    user_visible = filter_by_user_access(base, normal_user).all()

    assert {c.id for c in admin_visible} == {comic_a.id, comic_b.id}
    assert [c.id for c in user_visible] == [comic_a.id]

    assert sorted(["10", "2", "10a", "1"], key=natural_sort_key) == ["1", "2", "10", "10a"]


def test_search_comics_delegates_to_search_service(auth_client):
    expected = {
        "total": 1,
        "limit": 50,
        "offset": 0,
        "results": [
            {
                "id": 123,
                "series": "Delegation Series",
                "volume": 1,
                "number": "1",
                "title": "Delegation Issue",
                "year": 2024,
                "publisher": "Publisher",
                "format": None,
                "thumbnail_path": None,
                "community_rating": None,
                "parker_rating_average": None,
                "parker_rating_count": 0,
                "rating_mode": "none",
                "rating_value": None,
                "rating_label": None,
                "progress_percentage": None,
            }
        ],
    }

    with patch("app.api.comics.SearchService") as mock_service_cls:
        mock_service_cls.return_value.search.return_value = expected

        response = auth_client.post(
            "/api/comics/search",
            json={
                "match": "all",
                "filters": [],
                "sort_by": "created",
                "sort_order": "desc",
                "limit": 50,
                "offset": 0,
            },
        )

    assert response.status_code == 200
    assert response.json() == expected
    mock_service_cls.return_value.search.assert_called_once()


def test_search_comics_can_sort_and_filter_by_parker_rating(auth_client, db, normal_user):
    library, _, volume = _create_graph(db, lib_name="comic-search-rating", series_name="Search Rating Saga")

    top_rated = create_comic(
        db, volume, library.active_root, "top-rated.cbz",
        number="1",
        title="Top Rated",
        community_rating=4.8,
        filename="top-rated.cbz",
    )
    lower_rated = create_comic(
        db, volume, library.active_root, "lower-rated.cbz",
        number="2",
        title="Lower Rated",
        community_rating=4.9,
        filename="lower-rated.cbz",
    )
    second_user = User(
        username="search-second-rater",
        email="search-second-rater@example.com",
        hashed_password="fakehash",
        is_superuser=False,
        is_active=True,
    )

    db.add_all([top_rated, lower_rated, second_user])
    db.flush()
    db.add_all([
        UserComicRating(user_id=normal_user.id, comic_id=top_rated.id, rating=5),
        UserComicRating(user_id=second_user.id, comic_id=top_rated.id, rating=4),
        UserComicRating(user_id=normal_user.id, comic_id=lower_rated.id, rating=3),
        UserComicRating(user_id=second_user.id, comic_id=lower_rated.id, rating=2),
    ])
    normal_user.accessible_libraries.append(library)
    db.commit()

    response = auth_client.post(
        "/api/comics/search",
        json={
            "match": "all",
            "filters": [
                {"field": "parker_rating", "operator": "at_least", "value": 4}
            ],
            "sort_by": "parker_rating",
            "sort_order": "desc",
            "limit": 10,
            "offset": 0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [item["title"] for item in payload["results"]] == ["Top Rated"]
    assert payload["results"][0]["rating_mode"] == "parker"
    assert payload["results"][0]["rating_value"] == 4.5
    assert payload["results"][0]["parker_rating_average"] == 4.5
    assert payload["results"][0]["parker_rating_count"] == 2


def test_get_comic_detail_returns_metadata_and_in_progress_status(auth_client, db, normal_user):
    library, series, volume = _create_graph(db, lib_name="comic-detail", series_name="Detail Saga")

    comic = create_comic(
        db, volume, library.active_root, "detail-7.cbz",
        number="7",
        title="Detail Issue",
        summary="Issue summary",
        web="https://comicvine.gamespot.com/detail-issue/4000-7/",
        page_count=20,
        publisher="Detail Pub",
        imprint="Detail Imprint",
        age_rating="Teen",
        language_iso="en",
        community_rating=3.8,
        filename="detail-7.cbz",
    )

    writer = Person(name="Detail Writer")
    penciller = Person(name="Detail Penciller")
    hero = Character(name="Detail Hero")
    team = Team(name="Detail Team")
    location = Location(name="Detail City")
    genre = Genre(name="Detail Genre")
    db.add_all([writer, penciller, hero, team, location, genre])
    db.flush()

    db.add_all([
        ComicCredit(comic_id=comic.id, person_id=writer.id, role="writer"),
        ComicCredit(comic_id=comic.id, person_id=penciller.id, role="penciller"),
    ])

    comic.characters.append(hero)
    comic.teams.append(team)
    comic.locations.append(location)
    comic.genres.append(genre)

    normal_user.accessible_libraries.append(library)
    db.flush()

    db.add(
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=comic.id,
            current_page=5,
            total_pages=20,
            completed=False,
        )
    )
    db.add_all([
        Bookmark(user_id=normal_user.id, comic_id=comic.id, page_index=7, label="Later"),
        Bookmark(user_id=normal_user.id, comic_id=comic.id, page_index=2, label="Earlier"),
    ])
    db.commit()

    response = auth_client.get(f"/api/comics/{comic.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == comic.id
    assert payload["series_id"] == series.id
    assert payload["library_id"] == library.id
    assert payload["credits"] == {
        "writer": ["Detail Writer"],
        "penciller": ["Detail Penciller"],
    }
    assert payload["characters"] == ["Detail Hero"]
    assert payload["teams"] == ["Detail Team"]
    assert payload["locations"] == ["Detail City"]
    assert payload["genres"] == ["Detail Genre"]
    assert payload["read_status"] == "in_progress"
    assert payload["resume_page"] == 5
    assert payload["bookmarks"] == [
        {"id": payload["bookmarks"][0]["id"], "page_index": 2, "label": "Earlier"},
        {"id": payload["bookmarks"][1]["id"], "page_index": 7, "label": "Later"},
    ]
    assert payload["web"] == "https://comicvine.gamespot.com/detail-issue/4000-7/"
    assert payload["web_label"] == "ComicVine"
    assert payload["web_title"] == "View on ComicVine"
    assert payload["source_rating"] == 3.8
    assert payload["parker_rating_average"] is None
    assert payload["parker_rating_count"] == 0
    assert payload["user_rating"] is None
    assert payload["parker_readers_count"] is None


def test_get_comic_detail_only_exposes_file_path_to_admins(client, db, normal_user, admin_user):
    library, _, volume = _create_graph(db, lib_name="comic-detail-file-path", series_name="File Path Detail Saga")

    comic = create_comic(
        db,
        volume,
        library.active_root,
        "file-path-1.cbz",
        number="1",
        title="File Path Issue",
        filename="file-path-1.cbz",
    )

    normal_user.accessible_libraries.append(library)
    db.commit()

    app.dependency_overrides[get_current_user] = lambda: normal_user
    user_response = client.get(f"/api/comics/{comic.id}")
    assert user_response.status_code == 200
    assert user_response.json()["file_path"] is None

    app.dependency_overrides[get_current_user] = lambda: admin_user
    admin_response = client.get(f"/api/comics/{comic.id}")
    assert admin_response.status_code == 200
    assert admin_response.json()["file_path"] == comic.absolute_path


def test_get_comic_detail_returns_completed_read_status(auth_client, db, normal_user):
    library, _, volume = _create_graph(db, lib_name="comic-detail-completed", series_name="Completed Detail Saga")

    comic = create_comic(
        db, volume, library.active_root, "completed-4.cbz",
        number="4",
        title="Completed Issue",
        page_count=18,
        filename="completed-4.cbz",
    )
    normal_user.accessible_libraries.append(library)
    db.flush()

    db.add(
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=comic.id,
            current_page=17,
            total_pages=18,
            completed=True,
        )
    )
    db.commit()

    response = auth_client.get(f"/api/comics/{comic.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["read_status"] == "completed"
    assert payload["resume_page"] is None


def test_get_comic_detail_sorts_tag_metadata_alphabetically(auth_client, db, normal_user):
    library, _, volume = _create_graph(db, lib_name="comic-detail-tags", series_name="Sorted Detail Saga")

    comic = create_comic(
        db, volume, library.active_root, "sorted-tags-12.cbz",
        number="12",
        title="Sorted Tags Issue",
        filename="sorted-tags-12.cbz",
    )

    hero_z = Character(name="Zeta Hero")
    hero_a = Character(name="Alpha Hero")
    team_z = Team(name="Zeta Team")
    team_a = Team(name="Alpha Team")
    location_z = Location(name="Zeta City")
    location_a = Location(name="Alpha City")
    genre_z = Genre(name="Zeta Genre")
    genre_a = Genre(name="Alpha Genre")
    db.add_all([hero_z, hero_a, team_z, team_a, location_z, location_a, genre_z, genre_a])
    db.flush()

    comic.characters.extend([hero_z, hero_a])
    comic.teams.extend([team_z, team_a])
    comic.locations.extend([location_z, location_a])
    comic.genres.extend([genre_z, genre_a])

    normal_user.accessible_libraries.append(library)
    db.commit()

    response = auth_client.get(f"/api/comics/{comic.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["characters"] == ["Alpha Hero", "Zeta Hero"]
    assert payload["teams"] == ["Alpha Team", "Zeta Team"]
    assert payload["locations"] == ["Alpha City", "Zeta City"]
    assert payload["genres"] == ["Alpha Genre", "Zeta Genre"]


def test_get_comic_detail_uses_generic_label_for_non_comicvine_web_links(auth_client, db, normal_user):
    library, _, volume = _create_graph(db, lib_name="comic-web-link", series_name="Alt Link Saga")

    comic = create_comic(
        db, volume, library.active_root, "alt-link-11.cbz",
        number="11",
        title="Alt Link Issue",
        web="https://leagueofcomicgeeks.com/comic/123456/alt-link-issue",
        filename="alt-link-11.cbz",
    )

    normal_user.accessible_libraries.append(library)
    db.commit()

    response = auth_client.get(f"/api/comics/{comic.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["web"] == "https://leagueofcomicgeeks.com/comic/123456/alt-link-issue"
    assert payload["web_label"] == "Web Link"
    assert payload["web_title"] == "Open web link"


def test_get_comic_detail_exposes_opted_in_completed_reader_count(auth_client, db, normal_user):
    library, _, volume = _create_graph(db, lib_name="comic-social", series_name="Social Saga")

    comic = create_comic(
        db, volume, library.active_root, "social-3.cbz",
        number="3",
        title="Social Issue",
        filename="social-3.cbz",
        page_count=24,
    )

    reader_a = User(
        username="social-reader-a",
        email="social-reader-a@example.com",
        hashed_password="fakehash",
        social_insights_enabled=True,
        is_active=True,
    )
    reader_b = User(
        username="social-reader-b",
        email="social-reader-b@example.com",
        hashed_password="fakehash",
        social_insights_enabled=True,
        is_active=True,
    )
    hidden_reader = User(
        username="social-reader-hidden",
        email="social-reader-hidden@example.com",
        hashed_password="fakehash",
        social_insights_enabled=False,
        is_active=True,
    )
    in_progress_only = User(
        username="social-reader-progress",
        email="social-reader-progress@example.com",
        hashed_password="fakehash",
        social_insights_enabled=True,
        is_active=True,
    )
    db.add_all([reader_a, reader_b, hidden_reader, in_progress_only])
    normal_user.accessible_libraries.append(library)
    db.flush()

    db.add_all([
        ReadingProgress(user_id=reader_a.id, comic_id=comic.id, current_page=24, total_pages=24, completed=True),
        ReadingProgress(user_id=reader_b.id, comic_id=comic.id, current_page=24, total_pages=24, completed=True),
        ReadingProgress(user_id=hidden_reader.id, comic_id=comic.id, current_page=24, total_pages=24, completed=True),
        ReadingProgress(user_id=in_progress_only.id, comic_id=comic.id, current_page=8, total_pages=24, completed=False),
    ])
    db.commit()

    response = auth_client.get(f"/api/comics/{comic.id}")

    assert response.status_code == 200
    assert response.json()["parker_readers_count"] == 2


def test_set_comic_rating_creates_and_updates_single_user_row(auth_client, db, normal_user):
    library, _, volume = _create_graph(db, lib_name="comic-rate", series_name="Rate Saga")
    comic = create_comic(
        db, volume, library.active_root, "rate-me.cbz",
        number="1",
        title="Rate Me",
        filename="rate-me.cbz",
    )

    other_user = User(
        username="other-rater",
        email="other-rater@example.com",
        hashed_password="fakehash",
        is_superuser=False,
        is_active=True,
    )
    db.add(other_user)

    normal_user.accessible_libraries.append(library)
    db.commit()

    create_response = auth_client.put(f"/api/comics/{comic.id}/rating", json={"rating": 4})

    assert create_response.status_code == 200
    assert create_response.json() == {
        "parker_rating_average": 4.0,
        "parker_rating_count": 1,
        "user_rating": 4,
    }

    rows = db.query(UserComicRating).filter(UserComicRating.user_id == normal_user.id, UserComicRating.comic_id == comic.id).all()
    assert len(rows) == 1
    assert rows[0].rating == 4

    db.add(UserComicRating(user_id=other_user.id, comic_id=comic.id, rating=2))
    db.commit()

    update_response = auth_client.put(f"/api/comics/{comic.id}/rating", json={"rating": 5})

    assert update_response.status_code == 200
    assert update_response.json() == {
        "parker_rating_average": 3.5,
        "parker_rating_count": 2,
        "user_rating": 5,
    }

    rows = db.query(UserComicRating).filter(UserComicRating.user_id == normal_user.id, UserComicRating.comic_id == comic.id).all()
    assert len(rows) == 1
    assert rows[0].rating == 5


def test_delete_comic_rating_updates_aggregate(auth_client, db, normal_user):
    library, _, volume = _create_graph(db, lib_name="comic-unrate", series_name="Unrate Saga")
    comic = create_comic(
        db, volume, library.active_root, "unrate-me.cbz",
        number="1",
        title="Unrate Me",
        filename="unrate-me.cbz",
    )

    other_user = User(
        username="other-unrater",
        email="other-unrater@example.com",
        hashed_password="fakehash",
        is_superuser=False,
        is_active=True,
    )
    db.add(other_user)
    db.flush()

    db.add_all([
        UserComicRating(user_id=normal_user.id, comic_id=comic.id, rating=4),
        UserComicRating(user_id=other_user.id, comic_id=comic.id, rating=2),
    ])
    normal_user.accessible_libraries.append(library)
    db.commit()

    response = auth_client.delete(f"/api/comics/{comic.id}/rating")

    assert response.status_code == 200
    assert response.json() == {
        "parker_rating_average": 2.0,
        "parker_rating_count": 1,
        "user_rating": None,
    }

    assert db.query(UserComicRating).filter(
        UserComicRating.user_id == normal_user.id,
        UserComicRating.comic_id == comic.id,
    ).first() is None


def test_rating_endpoints_respect_hidden_and_age_restricted_comics(auth_client, db, normal_user):
    hidden_library, _, hidden_volume = _create_graph(db, lib_name="comic-rate-hidden", series_name="Hidden Rate Saga")
    hidden_comic = create_comic(
        db, hidden_volume, hidden_library.active_root, "hidden-rate.cbz",
        number="1",
        title="Hidden Rate",
        filename="hidden-rate.cbz",
    )

    safe_library, _, safe_volume = _create_graph(db, lib_name="comic-rate-safe", series_name="Safe Rate Saga")
    mature_comic = create_comic(
        db, safe_volume, safe_library.active_root, "mature-rate.cbz",
        number="2",
        title="Mature Rate",
        age_rating="Mature 17+",
        filename="mature-rate.cbz",
    )

    normal_user.accessible_libraries.append(safe_library)
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    hidden_response = auth_client.put(f"/api/comics/{hidden_comic.id}/rating", json={"rating": 4})
    assert hidden_response.status_code == 404
    assert hidden_response.json() == {"detail": "Comic not found"}

    restricted_response = auth_client.put(f"/api/comics/{mature_comic.id}/rating", json={"rating": 4})
    assert restricted_response.status_code == 403
    assert restricted_response.json() == {"detail": "Content restricted by age rating"}


def test_get_comic_detail_missing_or_hidden_returns_404(auth_client, db):
    response = auth_client.get("/api/comics/999999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Comic not found"}

    library, _, volume = _create_graph(db, lib_name="comic-hidden", series_name="Hidden Saga")
    comic = create_comic(
        db, volume, library.active_root, "hidden.cbz",
        number="1",
        title="Hidden",
        filename="hidden.cbz",
    )
    db.commit()

    hidden = auth_client.get(f"/api/comics/{comic.id}")
    assert hidden.status_code == 404
    assert hidden.json() == {"detail": "Comic not found"}


def test_get_comic_thumbnail_requires_authentication(client, db):
    library, _, volume = _create_graph(db, lib_name="comic-thumb-auth", series_name="Thumb Auth Saga")
    comic = create_comic(
        db,
        volume,
        library.active_root,
        "auth-thumb.cbz",
        number="1",
        title="Auth Thumb",
        filename="auth-thumb.cbz",
    )
    db.commit()

    response = client.get(f"/api/comics/{comic.id}/thumbnail")

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


def test_get_comic_thumbnail_hides_inaccessible_library_comics(auth_client, db, tmp_path):
    library, _, volume = _create_graph(db, lib_name="comic-thumb-hidden", series_name="Hidden Thumb Saga")
    db_thumb = tmp_path / "hidden-thumb.webp"
    db_thumb.write_bytes(b"hidden-thumb")
    comic = create_comic(
        db,
        volume,
        library.active_root,
        "hidden-thumb.cbz",
        number="1",
        title="Hidden Thumb",
        thumbnail_path=str(db_thumb),
        filename="hidden-thumb.cbz",
    )
    db.commit()

    response = auth_client.get(f"/api/comics/{comic.id}/thumbnail")

    assert response.status_code == 404
    assert response.json() == {"detail": "Comic not found"}


def test_get_comic_thumbnail_db_path_and_fallback_and_missing(auth_client, db, normal_user, tmp_path):
    library, _, volume = _create_graph(db, lib_name="comic-thumb", series_name="Thumb Saga")

    db_thumb = tmp_path / "db-thumb.webp"
    db_thumb.write_bytes(b"db-thumb")

    comic_db = create_comic(
        db, volume, library.active_root, "db-thumb.cbz",
        number="1",
        title="DB Thumb",
        thumbnail_path=str(db_thumb),
        filename="db-thumb.cbz",
    )
    comic_std = create_comic(
        db, volume, library.active_root, "std-thumb.cbz",
        number="2",
        title="Std Thumb",
        thumbnail_path=None,
        filename="std-thumb.cbz",
    )
    comic_missing = create_comic(
        db, volume, library.active_root, "no-thumb.cbz",
        number="3",
        title="No Thumb",
        thumbnail_path=None,
        filename="no-thumb.cbz",
    )
    normal_user.accessible_libraries.append(library)
    db.commit()

    db_resp = auth_client.get(f"/api/comics/{comic_db.id}/thumbnail")
    assert db_resp.status_code == 200
    assert db_resp.headers["content-type"].startswith("image/webp")
    assert "ETag" in db_resp.headers

    standard_dir = Path("storage/cover")
    standard_dir.mkdir(parents=True, exist_ok=True)
    standard_path = standard_dir / f"comic_{comic_std.id}.webp"
    standard_backup = standard_path.read_bytes() if standard_path.exists() else None
    standard_path.write_bytes(b"standard-thumb")

    try:
        std_resp = auth_client.get(f"/api/comics/{comic_std.id}/thumbnail")
        assert std_resp.status_code == 200
        assert std_resp.headers["content-type"].startswith("image/webp")
    finally:
        if standard_backup is None:
            if standard_path.exists():
                standard_path.unlink()
        else:
            standard_path.write_bytes(standard_backup)

    missing_standard_path = standard_dir / f"comic_{comic_missing.id}.webp"
    missing_backup = missing_standard_path.read_bytes() if missing_standard_path.exists() else None
    if missing_standard_path.exists():
        missing_standard_path.unlink()

    try:
        missing_resp = auth_client.get(f"/api/comics/{comic_missing.id}/thumbnail")
        assert missing_resp.status_code == 404
        assert missing_resp.json() == {"detail": "Could not find thumbnail"}
    finally:
        if missing_backup is not None:
            missing_standard_path.write_bytes(missing_backup)


def test_random_backgrounds_requires_authentication(client):
    response = client.get("/api/comics/random/backgrounds?limit=3")

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


def test_random_backgrounds_handles_empty_and_limit(auth_client, db, normal_user):
    empty = auth_client.get("/api/comics/random/backgrounds?limit=3")
    assert empty.status_code == 200
    assert empty.json() == []

    library, _, volume = _create_graph(db, lib_name="comic-random", series_name="Random Saga")
    root = library.active_root
    c1 = create_comic(db, volume, root, "r1.cbz", number="1", title="R1", thumbnail_path="/tmp/r1.webp", filename="r1.cbz")
    c2 = create_comic(db, volume, root, "r2.cbz", number="2", title="R2", thumbnail_path="/tmp/r2.webp", filename="r2.cbz")
    c3 = create_comic(db, volume, root, "r3.cbz", number="3", title="R3", thumbnail_path="/tmp/r3.webp", filename="r3.cbz")
    normal_user.accessible_libraries.append(library)
    db.commit()

    with patch("app.api.comics.random.sample", side_effect=lambda rows, size: rows[:size]):
        response = auth_client.get("/api/comics/random/backgrounds?limit=2")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert payload[0].startswith(f"/api/comics/{c1.id}/thumbnail?v=")
    assert payload[1].startswith(f"/api/comics/{c2.id}/thumbnail?v=")


def test_random_backgrounds_filters_library_and_age_access(auth_client, db, normal_user):
    allowed_library, _, allowed_volume = _create_graph(db, lib_name="comic-random-allowed", series_name="Allowed Random")
    hidden_library, _, hidden_volume = _create_graph(db, lib_name="comic-random-hidden", series_name="Hidden Random")

    allowed_comic = create_comic(
        db,
        allowed_volume,
        allowed_library.active_root,
        "allowed-random.cbz",
        number="1",
        title="Allowed Random",
        age_rating="Everyone",
        thumbnail_path="/tmp/allowed-random.webp",
        filename="allowed-random.cbz",
    )
    create_comic(
        db,
        allowed_volume,
        allowed_library.active_root,
        "mature-random.cbz",
        number="2",
        title="Mature Random",
        age_rating="Mature 17+",
        thumbnail_path="/tmp/mature-random.webp",
        filename="mature-random.cbz",
    )
    create_comic(
        db,
        hidden_volume,
        hidden_library.active_root,
        "hidden-random.cbz",
        number="1",
        title="Hidden Random",
        age_rating="Everyone",
        thumbnail_path="/tmp/hidden-random.webp",
        filename="hidden-random.cbz",
    )
    normal_user.max_age_rating = "Teen"
    normal_user.accessible_libraries.append(allowed_library)
    db.commit()

    with patch("app.api.comics.random.sample", side_effect=lambda rows, size: rows[:size]):
        response = auth_client.get("/api/comics/random/backgrounds?limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0].startswith(f"/api/comics/{allowed_comic.id}/thumbnail?v=")


def test_cover_manifest_volume_and_series_reverse_sort(auth_client, db, normal_user):
    library, series, volume = _create_graph(db, lib_name="comic-manifest-rev", series_name="Countdown")

    issue_one = create_comic(
        db, volume, library.active_root, "cd-1.cbz",
        number="1",
        year=2020,
        title="Countdown #1",
        thumbnail_path="/tmp/cd-1.webp",
        filename="cd-1.cbz",
    )
    issue_four = create_comic(
        db, volume, library.active_root, "cd-4.cbz",
        number="4",
        year=2020,
        title="Countdown #4",
        thumbnail_path="/tmp/cd-4.webp",
        filename="cd-4.cbz",
    )

    normal_user.accessible_libraries.append(library)
    db.commit()

    by_volume = auth_client.get(f"/api/comics/covers/manifest?context_type=volume&context_id={volume.id}")
    assert by_volume.status_code == 200
    assert [item["label"] for item in by_volume.json()["items"]] == ["Countdown #4", "Countdown #1"]

    by_series = auth_client.get(f"/api/comics/covers/manifest?context_type=series&context_id={series.id}")
    assert by_series.status_code == 200
    assert [item["label"] for item in by_series.json()["items"]] == ["Countdown #4", "Countdown #1"]


def test_cover_manifest_zero_hour_uses_reverse_issue_numbers_before_dates(auth_client, db, normal_user):
    library, series, volume = _create_graph(
        db,
        lib_name="comic-manifest-zero-hour",
        series_name="Zero Hour: Crisis in Time",
    )

    issues_by_number = {
        number: create_comic(
            db,
            volume,
            library.active_root,
            f"zh-{number}.cbz",
            number=str(number),
            title=f"Zero Hour #{number}",
            year=1994,
            month=5 + number,
            day=1,
            thumbnail_path=f"/tmp/zh-{number}.webp",
            filename=f"zh-{number}.cbz",
        )
        for number in range(0, 5)
    }

    normal_user.accessible_libraries.append(library)
    db.commit()

    expected_ids = [issues_by_number[number].id for number in (4, 3, 2, 1, 0)]

    by_volume = auth_client.get(f"/api/comics/covers/manifest?context_type=volume&context_id={volume.id}")
    assert by_volume.status_code == 200
    assert [item["comic_id"] for item in by_volume.json()["items"]] == expected_ids

    by_series = auth_client.get(f"/api/comics/covers/manifest?context_type=series&context_id={series.id}")
    assert by_series.status_code == 200
    assert [item["comic_id"] for item in by_series.json()["items"]] == expected_ids


def test_cover_manifest_prefers_issue_number_before_release_dates(auth_client, db, normal_user):
    library = create_library_with_root(db, "comic-manifest-release-date", "/tmp/comic-manifest-release-date")
    root = library.active_root
    series = Series(name="Hawk & Dove", library=library)
    volume_two = Volume(series=series, volume_number=2)
    volume_three = Volume(series=series, volume_number=3)
    db.add_all([series, volume_two, volume_three])
    db.flush()

    volume_two_issues = [
        create_comic(
            db,
            volume_two,
            root,
            f"hawk-dove-v2-{number}.cbz",
            number=str(number),
            title=f"Hawk & Dove #{number}",
            year=1988,
            month={1: 7, 2: 8, 3: 9, 4: 5, 5: 6}[number],
            day=1,
            thumbnail_path=f"/tmp/hawk-dove-v2-{number}.webp",
            filename=f"hawk-dove-v2-{number}.cbz",
        )
        for number in range(1, 6)
    ]
    volume_three_issues = [
        create_comic(
            db,
            volume_three,
            root,
            f"hawk-dove-v3-{number}.cbz",
            number=str(number),
            title=f"Hawk & Dove #{number}",
            year=1991,
            thumbnail_path=f"/tmp/hawk-dove-v3-{number}.webp",
            filename=f"hawk-dove-v3-{number}.cbz",
        )
        for number in range(1, 11)
    ]

    normal_user.accessible_libraries.append(library)
    db.commit()

    by_volume = auth_client.get(f"/api/comics/covers/manifest?context_type=volume&context_id={volume_two.id}")
    assert by_volume.status_code == 200
    assert [item["comic_id"] for item in by_volume.json()["items"]] == [
        comic.id for comic in volume_two_issues
    ]

    by_series = auth_client.get(f"/api/comics/covers/manifest?context_type=series&context_id={series.id}")
    assert by_series.status_code == 200
    assert [item["comic_id"] for item in by_series.json()["items"]] == [
        comic.id for comic in [*volume_two_issues, *volume_three_issues]
    ]


def test_cover_manifest_paginates_large_contexts(auth_client, db, normal_user):
    library, _, volume = _create_graph(db, lib_name="comic-manifest-paged", series_name="Paged Manifest")

    comics = [
        create_comic(
            db,
            volume,
            library.active_root,
            f"paged-{number}.cbz",
            number=str(number),
            year=2025,
            title=f"Paged #{number}",
            thumbnail_path=f"/tmp/paged-{number}.webp",
            filename=f"paged-{number}.cbz",
        )
        for number in range(1, 6)
    ]

    normal_user.accessible_libraries.append(library)
    db.commit()

    first_page = auth_client.get(
        f"/api/comics/covers/manifest?context_type=volume&context_id={volume.id}&limit=2"
    )
    assert first_page.status_code == 200
    first_payload = first_page.json()
    assert first_payload["total"] == 5
    assert first_payload["offset"] == 0
    assert first_payload["limit"] == 2
    assert first_payload["has_more"] is True
    assert [item["comic_id"] for item in first_payload["items"]] == [comics[0].id, comics[1].id]

    final_page = auth_client.get(
        f"/api/comics/covers/manifest?context_type=volume&context_id={volume.id}&limit=2&offset=4"
    )
    assert final_page.status_code == 200
    final_payload = final_page.json()
    assert final_payload["total"] == 5
    assert final_payload["offset"] == 4
    assert final_payload["limit"] == 2
    assert final_payload["has_more"] is False
    assert [item["comic_id"] for item in final_payload["items"]] == [comics[4].id]


def test_cover_manifest_reading_list_pull_list_and_collection_ordering(auth_client, db, normal_user):
    library, series, volume = _create_graph(db, lib_name="comic-manifest-order", series_name="Manifest Order")

    c1 = create_comic(
        db, volume, library.active_root, "order-2.cbz",
        number="2",
        year=2022,
        title="Order #2",
        alternate_series="Manifest Reading",
        alternate_number="2",
        thumbnail_path="/tmp/order-2.webp",
        filename="order-2.cbz",
    )
    c2 = create_comic(
        db, volume, library.active_root, "order-1.cbz",
        number="1",
        year=2020,
        title="Order #1",
        alternate_series="Manifest Reading",
        alternate_number="1",
        thumbnail_path="/tmp/order-1.webp",
        filename="order-1.cbz",
    )
    c3 = create_comic(
        db, volume, library.active_root, "order-3.cbz",
        number="3",
        year=2021,
        title="Order #3",
        alternate_series="Manifest Reading",
        alternate_number="3",
        thumbnail_path="/tmp/order-3.webp",
        filename="order-3.cbz",
    )

    reading_list = ReadingList(name="Manifest Reading", description="")
    pull_list = PullList(user_id=normal_user.id, name="Manifest Pull")
    collection = Collection(name="Manifest Collection", description="")
    db.add_all([reading_list, pull_list, collection])
    db.flush()

    db.add_all([
        ReadingListItem(reading_list_id=reading_list.id, comic_id=c1.id, position=2),
        ReadingListItem(reading_list_id=reading_list.id, comic_id=c2.id, position=1),
        PullListItem(pull_list_id=pull_list.id, comic_id=c1.id, sort_order=20),
        PullListItem(pull_list_id=pull_list.id, comic_id=c3.id, sort_order=10),
        CollectionItem(collection_id=collection.id, comic_id=c1.id),
        CollectionItem(collection_id=collection.id, comic_id=c2.id),
        CollectionItem(collection_id=collection.id, comic_id=c3.id),
    ])

    normal_user.accessible_libraries.append(library)
    db.commit()

    reading_resp = auth_client.get(
        f"/api/comics/covers/manifest?context_type=reading_list&context_id={reading_list.id}"
    )
    assert reading_resp.status_code == 200
    assert [i["comic_id"] for i in reading_resp.json()["items"]] == [c2.id, c1.id]

    pull_resp = auth_client.get(
        f"/api/comics/covers/manifest?context_type=pull_list&context_id={pull_list.id}"
    )
    assert pull_resp.status_code == 200
    assert [i["comic_id"] for i in pull_resp.json()["items"]] == [c3.id, c1.id]

    collection_resp = auth_client.get(
        f"/api/comics/covers/manifest?context_type=collection&context_id={collection.id}"
    )
    assert collection_resp.status_code == 200
    assert [i["comic_id"] for i in collection_resp.json()["items"]] == [c2.id, c3.id, c1.id]


def test_cover_manifest_hides_items_outside_user_library(auth_client, db):
    library, _, volume = _create_graph(db, lib_name="comic-manifest-hidden", series_name="Hidden Manifest")
    comic = create_comic(
        db, volume, library.active_root, "hm-1.cbz",
        number="1",
        title="Hidden Manifest #1",
        year=2024,
        thumbnail_path="/tmp/hm-1.webp",
        filename="hm-1.cbz",
    )
    db.commit()

    response = auth_client.get(f"/api/comics/covers/manifest?context_type=volume&context_id={volume.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 0
    assert payload["items"] == []

