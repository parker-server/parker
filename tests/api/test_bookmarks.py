import pytest

from app.models.bookmark import Bookmark
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.user import User
from app.services.bookmarks import BookmarkService
from tests.factories import create_library_with_root


def _seed_bookmark_data(
    db,
    normal_user,
    *,
    lib_name: str = "bookmark-lib",
    series_name: str = "Bookmark Series",
    grant_access: bool = True,
):
    library = create_library_with_root(db, lib_name, f"/tmp/{lib_name}")
    root = library.active_root
    series = Series(name=series_name, library=library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="1",
        title=f"{series_name} #1",
        filename=f"{series_name}-1.cbz",
        library_root_id=root.id,
        relative_path=f"{series_name}-1.cbz",
        page_count=10,
    )

    db.add_all([series, volume, comic])
    db.flush()

    if grant_access:
        normal_user.accessible_libraries.append(library)
    db.commit()

    return {
        "library": library,
        "series": series,
        "volume": volume,
        "comic": comic,
    }


def _add_bookmark(db, normal_user, comic, *, page_index=3, label="Original"):
    bookmark = Bookmark(
        user_id=normal_user.id,
        comic_id=comic.id,
        page_index=page_index,
        label=label,
    )
    db.add(bookmark)
    db.commit()
    db.refresh(bookmark)
    return bookmark


def test_get_comic_bookmarks_returns_sorted_current_user_items(auth_client, db, normal_user):
    data = _seed_bookmark_data(db, normal_user, lib_name="bookmark-list", series_name="Bookmark List")

    other_user = User(
        username="bookmark-other",
        email="bookmark-other@example.com",
        hashed_password="fakehash",
        is_superuser=False,
        is_active=True,
    )
    db.add(other_user)
    db.flush()

    db.add_all([
        Bookmark(user_id=normal_user.id, comic_id=data["comic"].id, page_index=7, label="Later"),
        Bookmark(user_id=normal_user.id, comic_id=data["comic"].id, page_index=2, label="Early"),
        Bookmark(user_id=other_user.id, comic_id=data["comic"].id, page_index=1, label="Other User"),
    ])
    db.commit()

    response = auth_client.get(f"/api/bookmarks/comic/{data['comic'].id}")

    assert response.status_code == 200
    payload = response.json()
    assert [item["page_index"] for item in payload] == [2, 7]
    assert [item["label"] for item in payload] == ["Early", "Later"]


def test_bookmarks_hide_missing_and_inaccessible_comics(auth_client, db, normal_user):
    hidden_data = _seed_bookmark_data(
        db,
        normal_user,
        lib_name="bookmark-hidden",
        series_name="Bookmark Hidden",
        grant_access=False,
    )

    missing = auth_client.get("/api/bookmarks/comic/999999")
    hidden = auth_client.get(f"/api/bookmarks/comic/{hidden_data['comic'].id}")

    assert missing.status_code == 404
    assert missing.json()["detail"] == "Comic 999999 not found"
    assert hidden.status_code == 404
    assert hidden.json()["detail"] == f"Comic {hidden_data['comic'].id} not found"


def test_save_bookmark_creates_then_updates_same_page(auth_client, db, normal_user):
    data = _seed_bookmark_data(db, normal_user, lib_name="bookmark-save", series_name="Bookmark Save")

    created = auth_client.post(
        f"/api/bookmarks/comic/{data['comic'].id}",
        json={"page_index": 4, "label": "Splash"},
    )

    assert created.status_code == 200
    created_payload = created.json()
    assert created_payload["created"] is True
    assert created_payload["bookmark"]["page_index"] == 4
    assert created_payload["bookmark"]["label"] == "Splash"

    updated = auth_client.post(
        f"/api/bookmarks/comic/{data['comic'].id}",
        json={"page_index": 4, "label": "Better Splash"},
    )

    assert updated.status_code == 200
    updated_payload = updated.json()
    assert updated_payload["created"] is False
    assert updated_payload["bookmark"]["label"] == "Better Splash"

    bookmarks = db.query(Bookmark).filter(Bookmark.user_id == normal_user.id).all()
    assert len(bookmarks) == 1
    assert bookmarks[0].page_index == 4
    assert bookmarks[0].label == "Better Splash"


def test_save_bookmark_rejects_out_of_range_page(auth_client, db, normal_user):
    data = _seed_bookmark_data(db, normal_user, lib_name="bookmark-range", series_name="Bookmark Range")

    response = auth_client.post(
        f"/api/bookmarks/comic/{data['comic'].id}",
        json={"page_index": 99, "label": "Too Far"},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Bookmark page is out of range"}


def test_save_bookmark_restricted_comic_returns_403(auth_client, db, normal_user):
    data = _seed_bookmark_data(db, normal_user, lib_name="bookmark-restricted", series_name="Bookmark Restricted")

    data["comic"].age_rating = "Mature 17+"
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    response = auth_client.post(
        f"/api/bookmarks/comic/{data['comic'].id}",
        json={"page_index": 1, "label": "Blocked"},
    )

    assert response.status_code == 403
    assert "restricted" in response.json()["detail"].lower()


def test_save_bookmark_rolls_back_service_failure(auth_client, db, normal_user, monkeypatch):
    data = _seed_bookmark_data(db, normal_user, lib_name="bookmark-save-failure", series_name="Bookmark Save Failure")

    def fail_save(*args, **kwargs):
        raise RuntimeError("save failed")

    monkeypatch.setattr(BookmarkService, "save_bookmark", fail_save)

    response = auth_client.post(
        f"/api/bookmarks/comic/{data['comic'].id}",
        json={"page_index": 1, "label": "Blocked"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "save failed"
    assert db.query(Bookmark).filter(Bookmark.user_id == normal_user.id).count() == 0


def test_bookmark_service_raises_for_missing_mutations(db, normal_user):
    service = BookmarkService(db, user_id=normal_user.id)

    with pytest.raises(ValueError, match="Bookmark not found"):
        service.rename_bookmark(999999, "Nope")

    with pytest.raises(ValueError, match="Bookmark not found"):
        service.delete_bookmark(999999)


def test_update_and_delete_bookmark(auth_client, db, normal_user):
    data = _seed_bookmark_data(db, normal_user, lib_name="bookmark-mutate", series_name="Bookmark Mutate")

    bookmark = _add_bookmark(db, normal_user, data["comic"])

    patch_response = auth_client.patch(
        f"/api/bookmarks/{bookmark.id}",
        json={"label": "Updated"},
    )

    assert patch_response.status_code == 200
    assert patch_response.json()["label"] == "Updated"

    delete_response = auth_client.delete(f"/api/bookmarks/{bookmark.id}")

    assert delete_response.status_code == 200
    assert delete_response.json() == {"bookmark_id": bookmark.id, "message": "Bookmark deleted"}
    assert db.query(Bookmark).filter(Bookmark.id == bookmark.id).first() is None


def test_update_bookmark_returns_404_for_missing_bookmark(auth_client):
    response = auth_client.patch("/api/bookmarks/999999", json={"label": "Nope"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Bookmark not found"


def test_update_bookmark_maps_service_value_error(auth_client, db, normal_user, monkeypatch):
    data = _seed_bookmark_data(db, normal_user, lib_name="bookmark-update-value-error", series_name="Bookmark Update Value")
    bookmark = _add_bookmark(db, normal_user, data["comic"])

    def fail_rename(*args, **kwargs):
        raise ValueError("Bookmark vanished")

    monkeypatch.setattr(BookmarkService, "rename_bookmark", fail_rename)

    response = auth_client.patch(f"/api/bookmarks/{bookmark.id}", json={"label": "Updated"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Bookmark vanished"


def test_update_bookmark_maps_service_failure(auth_client, db, normal_user, monkeypatch):
    data = _seed_bookmark_data(db, normal_user, lib_name="bookmark-update-failure", series_name="Bookmark Update Failure")
    bookmark = _add_bookmark(db, normal_user, data["comic"])

    def fail_rename(*args, **kwargs):
        raise RuntimeError("rename failed")

    monkeypatch.setattr(BookmarkService, "rename_bookmark", fail_rename)

    response = auth_client.patch(f"/api/bookmarks/{bookmark.id}", json={"label": "Updated"})

    assert response.status_code == 500
    assert response.json()["detail"] == "rename failed"


def test_delete_bookmark_returns_404_for_missing_bookmark(auth_client):
    response = auth_client.delete("/api/bookmarks/999999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Bookmark not found"


def test_delete_bookmark_maps_service_value_error(auth_client, db, normal_user, monkeypatch):
    data = _seed_bookmark_data(db, normal_user, lib_name="bookmark-delete-value-error", series_name="Bookmark Delete Value")
    bookmark = _add_bookmark(db, normal_user, data["comic"])

    def fail_delete(*args, **kwargs):
        raise ValueError("Bookmark vanished")

    monkeypatch.setattr(BookmarkService, "delete_bookmark", fail_delete)

    response = auth_client.delete(f"/api/bookmarks/{bookmark.id}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Bookmark vanished"


def test_delete_bookmark_maps_service_failure(auth_client, db, normal_user, monkeypatch):
    data = _seed_bookmark_data(db, normal_user, lib_name="bookmark-delete-failure", series_name="Bookmark Delete Failure")
    bookmark = _add_bookmark(db, normal_user, data["comic"])

    def fail_delete(*args, **kwargs):
        raise RuntimeError("delete failed")

    monkeypatch.setattr(BookmarkService, "delete_bookmark", fail_delete)

    response = auth_client.delete(f"/api/bookmarks/{bookmark.id}")

    assert response.status_code == 500
    assert response.json()["detail"] == "delete failed"
