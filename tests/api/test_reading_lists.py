from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.series import Series


def _create_series_graph(db, *, lib_name: str, series_name: str, prefix: str):
    library = Library(name=lib_name, path=f"/tmp/{prefix}-lib")
    series = Series(name=series_name, library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([library, series, volume])
    db.flush()
    return library, series, volume


def _create_comic(db, *, volume_id: int, prefix: str, number: str, year: int, age_rating=None, title=None):
    comic = Comic(
        volume_id=volume_id,
        number=number,
        year=year,
        title=title or f"{prefix}-{number}",
        filename=f"{prefix}-{number}.cbz",
        file_path=f"/tmp/{prefix}-{number}.cbz",
        page_count=20,
        file_size=100,
        age_rating=age_rating,
    )
    db.add(comic)
    db.flush()
    return comic


def test_list_reading_lists_admin_shows_counts(admin_client, db):
    _, _, vol = _create_series_graph(
        db,
        lib_name="reading-lists-admin-lib",
        series_name="Reading Lists Admin Series",
        prefix="reading-lists-admin",
    )
    c1 = _create_comic(db, volume_id=vol.id, prefix="reading-lists-admin", number="1", year=2021)
    c2 = _create_comic(db, volume_id=vol.id, prefix="reading-lists-admin", number="2", year=2022)

    alpha = ReadingList(name="Alpha Reading List", description="A", auto_generated=0)
    beta = ReadingList(name="Beta Reading List", description="B", auto_generated=1)
    db.add_all([alpha, beta])
    db.flush()
    db.add_all([
        ReadingListItem(reading_list_id=alpha.id, comic_id=c1.id, position=1),
        ReadingListItem(reading_list_id=alpha.id, comic_id=c2.id, position=2),
        ReadingListItem(reading_list_id=beta.id, comic_id=c2.id, position=1),
    ])
    db.commit()

    response = admin_client.get("/api/reading-lists/?page=1&size=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert [item["name"] for item in payload["items"]] == ["Alpha Reading List", "Beta Reading List"]
    assert payload["items"][0]["comic_count"] == 2
    assert payload["items"][1]["comic_count"] == 1


def test_list_reading_lists_applies_library_and_banned_filters(auth_client, db, normal_user):
    allowed_lib, _, allowed_vol = _create_series_graph(
        db,
        lib_name="reading-lists-allowed-lib",
        series_name="Reading Lists Allowed Series",
        prefix="reading-lists-allowed",
    )
    safe = _create_comic(
        db,
        volume_id=allowed_vol.id,
        prefix="reading-lists-allowed",
        number="1",
        year=2021,
        age_rating="Teen",
    )

    _, _, mature_vol = _create_series_graph(
        db,
        lib_name="reading-lists-mature-lib",
        series_name="Reading Lists Mature Series",
        prefix="reading-lists-mature",
    )
    mature = _create_comic(
        db,
        volume_id=mature_vol.id,
        prefix="reading-lists-mature",
        number="1",
        year=2022,
        age_rating="Mature 17+",
    )

    hidden_lib, _, hidden_vol = _create_series_graph(
        db,
        lib_name="reading-lists-hidden-lib",
        series_name="Reading Lists Hidden Series",
        prefix="reading-lists-hidden",
    )
    hidden = _create_comic(
        db,
        volume_id=hidden_vol.id,
        prefix="reading-lists-hidden",
        number="1",
        year=2020,
        age_rating="Teen",
    )

    visible_list = ReadingList(name="Visible Reading List", auto_generated=0)
    mature_list = ReadingList(name="Mature Reading List", auto_generated=0)
    hidden_list = ReadingList(name="Hidden Reading List", auto_generated=0)
    db.add_all([visible_list, mature_list, hidden_list])
    db.flush()
    db.add_all([
        ReadingListItem(reading_list_id=visible_list.id, comic_id=safe.id, position=1),
        ReadingListItem(reading_list_id=mature_list.id, comic_id=mature.id, position=1),
        ReadingListItem(reading_list_id=hidden_list.id, comic_id=hidden.id, position=1),
    ])

    normal_user.accessible_libraries.append(allowed_lib)
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    response = auth_client.get("/api/reading-lists/?page=1&size=20")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["name"] == "Visible Reading List"


def test_get_reading_list_success_returns_position_order_and_details(auth_client, db, normal_user):
    lib, _, vol = _create_series_graph(
        db,
        lib_name="reading-lists-detail-lib",
        series_name="Reading Lists Detail Series",
        prefix="reading-lists-detail",
    )
    c1 = _create_comic(db, volume_id=vol.id, prefix="reading-lists-detail", number="1", year=2020)
    c2 = _create_comic(db, volume_id=vol.id, prefix="reading-lists-detail", number="2", year=2021)

    reading_list = ReadingList(name="Detail Reading List", description="Detail", auto_generated=0)
    db.add(reading_list)
    db.flush()
    db.add_all([
        ReadingListItem(reading_list_id=reading_list.id, comic_id=c2.id, position=2.0),
        ReadingListItem(reading_list_id=reading_list.id, comic_id=c1.id, position=1.0),
    ])

    normal_user.accessible_libraries.append(lib)
    db.commit()

    response = auth_client.get(f"/api/reading-lists/{reading_list.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Detail Reading List"
    assert payload["comic_count"] == 2
    assert [c["id"] for c in payload["comics"]] == [c1.id, c2.id]
    assert [c["position"] for c in payload["comics"]] == [1.0, 2.0]
    assert payload["details"] == {
        "writers": [],
        "pencillers": [],
        "characters": [],
        "teams": [],
        "locations": [],
    }


def test_get_reading_list_404_for_missing_list(auth_client):
    response = auth_client.get("/api/reading-lists/999999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Reading list not found"


def test_get_reading_list_404_when_user_has_no_visible_comics(auth_client, db):
    _, _, vol = _create_series_graph(
        db,
        lib_name="reading-lists-hidden-detail-lib",
        series_name="Reading Lists Hidden Detail Series",
        prefix="reading-lists-hidden-detail",
    )
    comic = _create_comic(db, volume_id=vol.id, prefix="reading-lists-hidden-detail", number="1", year=2020)

    reading_list = ReadingList(name="Hidden Detail Reading List", auto_generated=0)
    db.add(reading_list)
    db.flush()
    db.add(ReadingListItem(reading_list_id=reading_list.id, comic_id=comic.id, position=1))
    db.commit()

    response = auth_client.get(f"/api/reading-lists/{reading_list.id}")

    assert response.status_code == 404
    assert response.json()["detail"] == "No comics found (or access denied)"


def test_get_reading_list_restriction_blocks_banned_content(auth_client, db, normal_user):
    lib, _, vol = _create_series_graph(
        db,
        lib_name="reading-lists-ban-lib",
        series_name="Reading Lists Ban Series",
        prefix="reading-lists-ban",
    )
    mature = _create_comic(
        db,
        volume_id=vol.id,
        prefix="reading-lists-ban",
        number="1",
        year=2022,
        age_rating="Mature 17+",
    )

    reading_list = ReadingList(name="Restricted Reading List", auto_generated=0)
    db.add(reading_list)
    db.flush()
    db.add(ReadingListItem(reading_list_id=reading_list.id, comic_id=mature.id, position=1))

    normal_user.accessible_libraries.append(lib)
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    response = auth_client.get(f"/api/reading-lists/{reading_list.id}")

    assert response.status_code == 403
    assert "age-restricted" in response.json()["detail"].lower()


def test_delete_reading_list_success_and_missing(auth_client, db):
    reading_list = ReadingList(name="Delete Reading List", auto_generated=0)
    db.add(reading_list)
    db.commit()

    deleted = auth_client.delete(f"/api/reading-lists/{reading_list.id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"message": "Reading list 'Delete Reading List' deleted"}

    missing = auth_client.delete(f"/api/reading-lists/{reading_list.id}")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Reading list not found"
