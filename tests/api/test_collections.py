from app.models.collection import Collection, CollectionItem
from app.models.comic import Comic, Volume
from app.models.library import Library
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


def test_list_collections_admin_shows_counts(auth_client, admin_client, db):
    lib, _, vol = _create_series_graph(
        db,
        lib_name="collections-admin-lib",
        series_name="Collections Admin Series",
        prefix="collections-admin",
    )
    c1 = _create_comic(db, volume_id=vol.id, prefix="collections-admin", number="1", year=2021)
    c2 = _create_comic(db, volume_id=vol.id, prefix="collections-admin", number="2", year=2022)

    alpha = Collection(name="Alpha Collection", description="A", auto_generated=0)
    beta = Collection(name="Beta Collection", description="B", auto_generated=1)
    db.add_all([alpha, beta])
    db.flush()
    db.add_all([
        CollectionItem(collection_id=alpha.id, comic_id=c1.id),
        CollectionItem(collection_id=alpha.id, comic_id=c2.id),
        CollectionItem(collection_id=beta.id, comic_id=c2.id),
    ])
    db.commit()

    response = admin_client.get("/api/collections/?page=1&size=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert [item["name"] for item in payload["items"]] == ["Alpha Collection", "Beta Collection"]
    assert payload["items"][0]["comic_count"] == 2
    assert payload["items"][1]["comic_count"] == 1


def test_list_collections_applies_restrictions_and_poison_pill(auth_client, db, normal_user):
    lib, _, safe_vol = _create_series_graph(
        db,
        lib_name="collections-restrict-lib",
        series_name="Collections Safe Series",
        prefix="collections-restrict-safe",
    )
    safe_issue = _create_comic(db, volume_id=safe_vol.id, prefix="collections-restrict-safe", number="1", year=2020, age_rating="Teen")

    _, _, mixed_vol = _create_series_graph(
        db,
        lib_name="collections-restrict-lib-2",
        series_name="Collections Mixed Series",
        prefix="collections-restrict-mixed",
    )
    mixed_safe = _create_comic(db, volume_id=mixed_vol.id, prefix="collections-restrict-mixed", number="1", year=2021, age_rating="Teen")
    mixed_mature = _create_comic(db, volume_id=mixed_vol.id, prefix="collections-restrict-mixed", number="2", year=2021, age_rating="Mature 17+")

    col_safe = Collection(name="Safe Collection", auto_generated=0)
    col_poison = Collection(name="Poisoned Collection", auto_generated=0)
    col_explicit = Collection(name="Explicit Collection", auto_generated=0)
    db.add_all([col_safe, col_poison, col_explicit])
    db.flush()
    db.add_all([
        CollectionItem(collection_id=col_safe.id, comic_id=safe_issue.id),
        CollectionItem(collection_id=col_poison.id, comic_id=mixed_safe.id),
        CollectionItem(collection_id=col_explicit.id, comic_id=mixed_mature.id),
    ])

    normal_user.accessible_libraries.extend([lib, mixed_vol.series.library])
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    response = auth_client.get("/api/collections/?page=1&size=20")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["name"] == "Safe Collection"


def test_get_collection_success_returns_sorted_comics_and_details(auth_client, db, normal_user):
    lib, _, vol = _create_series_graph(
        db,
        lib_name="collections-detail-lib",
        series_name="Collections Detail Series",
        prefix="collections-detail",
    )
    newer = _create_comic(db, volume_id=vol.id, prefix="collections-detail", number="2", year=2022)
    older = _create_comic(db, volume_id=vol.id, prefix="collections-detail", number="1", year=2020)

    collection = Collection(name="Detail Collection", description="Detail", auto_generated=0)
    db.add(collection)
    db.flush()
    db.add_all([
        CollectionItem(collection_id=collection.id, comic_id=newer.id),
        CollectionItem(collection_id=collection.id, comic_id=older.id),
    ])

    normal_user.accessible_libraries.append(lib)
    db.commit()

    response = auth_client.get(f"/api/collections/{collection.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Detail Collection"
    assert payload["comic_count"] == 2
    assert [c["id"] for c in payload["comics"]] == [older.id, newer.id]
    assert payload["details"] == {
        "writers": [],
        "pencillers": [],
        "characters": [],
        "teams": [],
        "locations": [],
    }


def test_get_collection_404_for_missing_collection(auth_client):
    response = auth_client.get("/api/collections/999999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Collection not found"


def test_get_collection_404_when_user_has_no_visible_comics(auth_client, db):
    _, _, vol = _create_series_graph(
        db,
        lib_name="collections-hidden-lib",
        series_name="Collections Hidden Series",
        prefix="collections-hidden",
    )
    comic = _create_comic(db, volume_id=vol.id, prefix="collections-hidden", number="1", year=2020)

    collection = Collection(name="Hidden Collection", auto_generated=0)
    db.add(collection)
    db.flush()
    db.add(CollectionItem(collection_id=collection.id, comic_id=comic.id))
    db.commit()

    response = auth_client.get(f"/api/collections/{collection.id}")

    assert response.status_code == 404
    assert response.json()["detail"] == "No comics found"


def test_get_collection_restriction_blocks_banned_content(auth_client, db, normal_user):
    lib, _, vol = _create_series_graph(
        db,
        lib_name="collections-ban-lib",
        series_name="Collections Ban Series",
        prefix="collections-ban",
    )
    mature = _create_comic(
        db,
        volume_id=vol.id,
        prefix="collections-ban",
        number="1",
        year=2020,
        age_rating="Mature 17+",
    )

    collection = Collection(name="Restricted Collection", auto_generated=0)
    db.add(collection)
    db.flush()
    db.add(CollectionItem(collection_id=collection.id, comic_id=mature.id))

    normal_user.accessible_libraries.append(lib)
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    response = auth_client.get(f"/api/collections/{collection.id}")

    assert response.status_code == 403
    assert "age-restricted" in response.json()["detail"].lower()


def test_delete_collection_admin_success_and_missing(admin_client, db):
    collection = Collection(name="Delete Collection", auto_generated=0)
    db.add(collection)
    db.commit()

    deleted = admin_client.delete(f"/api/collections/{collection.id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"message": "Collection 'Delete Collection' deleted"}

    missing = admin_client.delete(f"/api/collections/{collection.id}")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Collection not found"
