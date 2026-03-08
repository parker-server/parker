from pathlib import Path
from unittest.mock import patch

from app.api.comics import filter_by_user_access, natural_sort_key
from app.models.collection import Collection, CollectionItem
from app.models.comic import Comic, Volume
from app.models.credits import ComicCredit, Person
from app.models.library import Library
from app.models.pull_list import PullList, PullListItem
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.reading_progress import ReadingProgress
from app.models.series import Series
from app.models.tags import Character, Genre, Location, Team


def _create_graph(db, *, lib_name: str, series_name: str):
    library = Library(name=lib_name, path=f"/tmp/{lib_name}")
    series = Series(name=series_name, library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([library, series, volume])
    db.flush()
    return library, series, volume


def test_filter_by_user_access_and_natural_sort_key(db, admin_user, normal_user):
    lib_a, _, vol_a = _create_graph(db, lib_name="comic-access-a", series_name="Access A")
    lib_b, _, vol_b = _create_graph(db, lib_name="comic-access-b", series_name="Access B")

    comic_a = Comic(
        volume_id=vol_a.id,
        number="1",
        title="A #1",
        filename="a-1.cbz",
        file_path="/tmp/access-a-1.cbz",
    )
    comic_b = Comic(
        volume_id=vol_b.id,
        number="1",
        title="B #1",
        filename="b-1.cbz",
        file_path="/tmp/access-b-1.cbz",
    )
    db.add_all([comic_a, comic_b])

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


def test_get_comic_detail_returns_metadata_and_in_progress_status(auth_client, db, normal_user):
    library, series, volume = _create_graph(db, lib_name="comic-detail", series_name="Detail Saga")

    comic = Comic(
        volume_id=volume.id,
        number="7",
        title="Detail Issue",
        summary="Issue summary",
        page_count=20,
        publisher="Detail Pub",
        imprint="Detail Imprint",
        age_rating="Teen",
        language_iso="en",
        filename="detail-7.cbz",
        file_path="/tmp/detail-7.cbz",
    )
    db.add(comic)
    db.flush()

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


def test_get_comic_detail_missing_or_hidden_returns_404(auth_client, db):
    response = auth_client.get("/api/comics/999999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Comic not found"}

    library, _, volume = _create_graph(db, lib_name="comic-hidden", series_name="Hidden Saga")
    comic = Comic(
        volume_id=volume.id,
        number="1",
        title="Hidden",
        filename="hidden.cbz",
        file_path="/tmp/hidden.cbz",
    )
    db.add_all([library, comic])
    db.commit()

    hidden = auth_client.get(f"/api/comics/{comic.id}")
    assert hidden.status_code == 404
    assert hidden.json() == {"detail": "Comic not found"}


def test_get_comic_thumbnail_db_path_and_fallback_and_missing(client, db, tmp_path):
    library, _, volume = _create_graph(db, lib_name="comic-thumb", series_name="Thumb Saga")

    db_thumb = tmp_path / "db-thumb.webp"
    db_thumb.write_bytes(b"db-thumb")

    comic_db = Comic(
        volume_id=volume.id,
        number="1",
        title="DB Thumb",
        thumbnail_path=str(db_thumb),
        filename="db-thumb.cbz",
        file_path="/tmp/db-thumb.cbz",
    )
    comic_std = Comic(
        volume_id=volume.id,
        number="2",
        title="Std Thumb",
        thumbnail_path=None,
        filename="std-thumb.cbz",
        file_path="/tmp/std-thumb.cbz",
    )
    comic_missing = Comic(
        volume_id=volume.id,
        number="3",
        title="No Thumb",
        thumbnail_path=None,
        filename="no-thumb.cbz",
        file_path="/tmp/no-thumb.cbz",
    )
    db.add_all([library, comic_db, comic_std, comic_missing])
    db.commit()

    db_resp = client.get(f"/api/comics/{comic_db.id}/thumbnail")
    assert db_resp.status_code == 200
    assert db_resp.headers["content-type"].startswith("image/webp")
    assert "ETag" in db_resp.headers

    standard_dir = Path("storage/cover")
    standard_dir.mkdir(parents=True, exist_ok=True)
    standard_path = standard_dir / f"comic_{comic_std.id}.webp"
    standard_backup = standard_path.read_bytes() if standard_path.exists() else None
    standard_path.write_bytes(b"standard-thumb")

    try:
        std_resp = client.get(f"/api/comics/{comic_std.id}/thumbnail")
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
        missing_resp = client.get(f"/api/comics/{comic_missing.id}/thumbnail")
        assert missing_resp.status_code == 404
        assert missing_resp.json() == {"detail": "Could not find thumbnail"}
    finally:
        if missing_backup is not None:
            missing_standard_path.write_bytes(missing_backup)


def test_random_backgrounds_handles_empty_and_limit(client, db):
    empty = client.get("/api/comics/random/backgrounds?limit=3")
    assert empty.status_code == 200
    assert empty.json() == []

    library, _, volume = _create_graph(db, lib_name="comic-random", series_name="Random Saga")
    c1 = Comic(volume_id=volume.id, number="1", title="R1", thumbnail_path="/tmp/r1.webp", filename="r1.cbz", file_path="/tmp/r1.cbz")
    c2 = Comic(volume_id=volume.id, number="2", title="R2", thumbnail_path="/tmp/r2.webp", filename="r2.cbz", file_path="/tmp/r2.cbz")
    c3 = Comic(volume_id=volume.id, number="3", title="R3", thumbnail_path="/tmp/r3.webp", filename="r3.cbz", file_path="/tmp/r3.cbz")
    db.add_all([library, c1, c2, c3])
    db.commit()

    with patch("app.api.comics.random.sample", side_effect=lambda rows, size: rows[:size]):
        response = client.get("/api/comics/random/backgrounds?limit=2")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert payload[0].startswith(f"/api/comics/{c1.id}/thumbnail?v=")
    assert payload[1].startswith(f"/api/comics/{c2.id}/thumbnail?v=")


def test_cover_manifest_volume_and_series_reverse_sort(auth_client, db, normal_user):
    library, series, volume = _create_graph(db, lib_name="comic-manifest-rev", series_name="Countdown")

    issue_one = Comic(
        volume_id=volume.id,
        number="1",
        year=2020,
        title="Countdown #1",
        thumbnail_path="/tmp/cd-1.webp",
        filename="cd-1.cbz",
        file_path="/tmp/cd-1.cbz",
    )
    issue_four = Comic(
        volume_id=volume.id,
        number="4",
        year=2020,
        title="Countdown #4",
        thumbnail_path="/tmp/cd-4.webp",
        filename="cd-4.cbz",
        file_path="/tmp/cd-4.cbz",
    )
    db.add_all([issue_one, issue_four])

    normal_user.accessible_libraries.append(library)
    db.commit()

    by_volume = auth_client.get(f"/api/comics/covers/manifest?context_type=volume&context_id={volume.id}")
    assert by_volume.status_code == 200
    assert [item["label"] for item in by_volume.json()["items"]] == ["Countdown #4", "Countdown #1"]

    by_series = auth_client.get(f"/api/comics/covers/manifest?context_type=series&context_id={series.id}")
    assert by_series.status_code == 200
    assert [item["label"] for item in by_series.json()["items"]] == ["Countdown #4", "Countdown #1"]


def test_cover_manifest_reading_list_pull_list_and_collection_ordering(auth_client, db, normal_user):
    library, series, volume = _create_graph(db, lib_name="comic-manifest-order", series_name="Manifest Order")

    c1 = Comic(
        volume_id=volume.id,
        number="2",
        year=2022,
        title="Order #2",
        thumbnail_path="/tmp/order-2.webp",
        filename="order-2.cbz",
        file_path="/tmp/order-2.cbz",
    )
    c2 = Comic(
        volume_id=volume.id,
        number="1",
        year=2020,
        title="Order #1",
        thumbnail_path="/tmp/order-1.webp",
        filename="order-1.cbz",
        file_path="/tmp/order-1.cbz",
    )
    c3 = Comic(
        volume_id=volume.id,
        number="3",
        year=2021,
        title="Order #3",
        thumbnail_path="/tmp/order-3.webp",
        filename="order-3.cbz",
        file_path="/tmp/order-3.cbz",
    )
    db.add_all([c1, c2, c3])
    db.flush()

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
    comic = Comic(
        volume_id=volume.id,
        number="1",
        title="Hidden Manifest #1",
        year=2024,
        thumbnail_path="/tmp/hm-1.webp",
        filename="hm-1.cbz",
        file_path="/tmp/hm-1.cbz",
    )
    db.add_all([library, comic])
    db.commit()

    response = auth_client.get(f"/api/comics/covers/manifest?context_type=volume&context_id={volume.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 0
    assert payload["items"] == []

