from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.api.users import MAX_AVATAR_SIZE_BYTES, get_stats_service
from app.core.security import get_password_hash, verify_password
from app.main import app
from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.pull_list import PullList, PullListItem
from app.models.reading_progress import ReadingProgress
from app.models.series import Series
from app.models.user import User


def _seed_user_activity(db, user):
    library = Library(name="Dash Library", path="/tmp/dash-lib")
    series = Series(name="Dashboard Series", library=library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="1",
        title="Dashboard Issue",
        filename="dashboard.cbz",
        file_path="/tmp/dashboard.cbz",
        page_count=10,
    )
    pull_list = PullList(user_id=user.id, name="Weekly Pulls")

    db.add_all([library, series, volume, comic, pull_list])
    db.flush()

    pull_item = PullListItem(pull_list_id=pull_list.id, comic_id=comic.id, sort_order=0)
    progress = ReadingProgress(
        user_id=user.id,
        comic_id=comic.id,
        current_page=3,
        total_pages=10,
        completed=False,
    )

    db.add_all([pull_item, progress])
    db.commit()


def test_user_dashboard_returns_expected_sections(auth_client, db, normal_user):
    _seed_user_activity(db, normal_user)

    with patch("app.api.users.SettingsService.get", return_value=True), \
         patch("app.api.users.StatisticsService.get_dashboard_payload", return_value={"stats": {"issues_read": 1}, "active_streak": 0}):
        response = auth_client.get("/api/users/me/dashboard")

    assert response.status_code == 200
    payload = response.json()
    assert payload["opds_enabled"] is True
    assert payload["user"]["username"] == normal_user.username
    assert len(payload["pull_lists"]) == 1
    assert payload["pull_lists"][0]["name"] == "Weekly Pulls"
    assert len(payload["continue_reading"]) == 1


def test_get_and_update_preferences(auth_client):
    initial = auth_client.get("/api/users/me/preferences")
    assert initial.status_code == 200
    assert initial.json()["share_progress_enabled"] is False

    update = auth_client.patch(
        "/api/users/me/preferences",
        json={"share_progress_enabled": True, "monthly_reading_goal": 15},
    )
    assert update.status_code == 200

    after = auth_client.get("/api/users/me/preferences")
    assert after.status_code == 200
    assert after.json() == {"share_progress_enabled": True, "monthly_reading_goal": 15}


def test_update_preferences_rejects_invalid_goal(auth_client):
    response = auth_client.patch("/api/users/me/preferences", json={"monthly_reading_goal": 0})

    assert response.status_code == 422


def test_update_password_success_and_incorrect_current(auth_client, db, normal_user):
    bad = auth_client.put(
        "/api/users/me/password",
        json={"current_password": "wrong", "new_password": "newpassword1"},
    )
    assert bad.status_code == 400
    assert bad.json()["detail"] == "Incorrect current password"

    good = auth_client.put(
        "/api/users/me/password",
        json={"current_password": "test1234", "new_password": "newpassword1"},
    )
    assert good.status_code == 200

    db.refresh(normal_user)
    assert verify_password("newpassword1", normal_user.hashed_password)


def test_year_in_review_uses_default_and_explicit_year(auth_client):
    class DummyStats:
        def get_year_wrapped(self, year):
            return {"year": year}

    app.dependency_overrides[get_stats_service] = lambda: DummyStats()

    default_response = auth_client.get("/api/users/me/year-in-review")
    explicit_response = auth_client.get("/api/users/me/year-in-review?year=2024")

    assert default_response.status_code == 200
    assert default_response.json()["year"] == datetime.now(timezone.utc).year
    assert explicit_response.status_code == 200
    assert explicit_response.json() == {"year": 2024}


def test_admin_create_user_and_duplicate_username(admin_client, db):
    lib = Library(name="Create User Library", path="/tmp/create-user-lib")
    db.add(lib)
    db.commit()

    create_response = admin_client.post(
        "/api/users/",
        json={
            "username": "ReaderOne",
            "email": "reader1@example.com",
            "password": "password123",
            "is_superuser": False,
            "library_ids": [lib.id],
            "max_age_rating": "Teen",
            "allow_unknown_age_ratings": True,
        },
    )

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["username"] == "ReaderOne"
    assert created["accessible_library_ids"] == [lib.id]
    assert created["max_age_rating"] == "Teen"
    assert created["allow_unknown_age_ratings"] is True

    dup_response = admin_client.post(
        "/api/users/",
        json={
            "username": "readerone",
            "email": "reader2@example.com",
            "password": "password123",
            "is_superuser": False,
            "library_ids": [],
        },
    )

    assert dup_response.status_code == 400
    assert dup_response.json()["detail"] == "Username already exists"


def test_admin_create_user_rejects_empty_fields(admin_client):
    response = admin_client.post(
        "/api/users/",
        json={
            "username": "   ",
            "email": "blank@example.com",
            "password": "password123",
            "is_superuser": False,
            "library_ids": [],
        },
    )

    assert response.status_code == 422


def test_admin_list_users_includes_library_ids(admin_client, db):
    lib = Library(name="List User Library", path="/tmp/list-user-lib")
    user = User(
        username="list-user",
        email="list-user@example.com",
        hashed_password=get_password_hash("password123"),
        is_superuser=False,
        is_active=True,
        accessible_libraries=[lib],
    )
    db.add_all([lib, user])
    db.commit()

    response = admin_client.get("/api/users/?page=1&size=100")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] >= 1

    listed = next(item for item in payload["items"] if item["username"] == "list-user")
    assert listed["accessible_library_ids"] == [lib.id]


def test_admin_update_user_handles_normal_and_superuser_modes(admin_client, db):
    lib_a = Library(name="Update Library A", path="/tmp/update-lib-a")
    lib_b = Library(name="Update Library B", path="/tmp/update-lib-b")
    user = User(
        username="target-user",
        email="target@example.com",
        hashed_password=get_password_hash("password123"),
        is_superuser=False,
        is_active=True,
    )
    db.add_all([lib_a, lib_b, user])
    db.commit()

    missing = admin_client.patch("/api/users/999999", json={"email": "none@example.com"})
    assert missing.status_code == 404

    update_normal = admin_client.patch(
        f"/api/users/{user.id}",
        json={
            "email": "updated@example.com",
            "password": "updatedpass123",
            "is_superuser": False,
            "is_active": False,
            "library_ids": [lib_a.id],
            "max_age_rating": "Teen",
            "allow_unknown_age_ratings": True,
        },
    )

    assert update_normal.status_code == 200

    db.refresh(user)
    assert user.email == "updated@example.com"
    assert user.is_active is False
    assert user.is_superuser is False
    assert verify_password("updatedpass123", user.hashed_password)
    assert [l.id for l in user.accessible_libraries] == [lib_a.id]
    assert user.max_age_rating == "Teen"
    assert user.allow_unknown_age_ratings is True

    update_super = admin_client.patch(
        f"/api/users/{user.id}",
        json={
            "email": "super@example.com",
            "is_superuser": True,
            "library_ids": [lib_b.id],
            "max_age_rating": "Mature 17+",
            "allow_unknown_age_ratings": True,
        },
    )

    assert update_super.status_code == 200

    db.refresh(user)
    assert user.is_superuser is True
    assert user.max_age_rating is None
    assert user.allow_unknown_age_ratings is False
    assert user.accessible_libraries == []


def test_admin_delete_user_guards_and_success(admin_client, admin_user, db):
    target = User(
        username="delete-target",
        email="delete-target@example.com",
        hashed_password=get_password_hash("password123"),
        is_superuser=False,
        is_active=True,
    )
    db.add(target)
    db.commit()

    cannot_self = admin_client.delete(f"/api/users/{admin_user.id}")
    assert cannot_self.status_code == 400
    assert cannot_self.json()["detail"] == "Cannot delete yourself"

    missing = admin_client.delete("/api/users/999999")
    assert missing.status_code == 404

    success = admin_client.delete(f"/api/users/{target.id}")
    assert success.status_code == 200
    assert success.json() == {"message": "User deleted"}

    deleted = db.query(User).filter(User.id == target.id).first()
    assert deleted is None


def test_upload_avatar_validation_and_processing_errors(auth_client, monkeypatch, tmp_path):
    monkeypatch.setattr("app.api.users.settings.avatar_dir", tmp_path / "avatars")

    bad_type = auth_client.post(
        "/api/users/me/avatar",
        files={"file": ("avatar.gif", b"GIF89a", "image/gif")},
    )
    assert bad_type.status_code == 400

    too_large = auth_client.post(
        "/api/users/me/avatar",
        files={"file": ("avatar.png", b"a" * (MAX_AVATAR_SIZE_BYTES + 1), "image/png")},
    )
    assert too_large.status_code == 413

    with patch("app.api.users.ImageService.process_avatar", return_value=False):
        processing_failed = auth_client.post(
            "/api/users/me/avatar",
            files={"file": ("avatar.png", b"small-png", "image/png")},
        )

    assert processing_failed.status_code == 500


def test_upload_avatar_success_and_get_avatar_flows(auth_client, db, normal_user, monkeypatch, tmp_path):
    monkeypatch.setattr("app.api.users.settings.avatar_dir", tmp_path / "avatars")

    def fake_process_avatar(content, file_path):
        Path(file_path).write_bytes(content)
        return True

    with patch("app.api.users.ImageService.process_avatar", side_effect=fake_process_avatar):
        upload = auth_client.post(
            "/api/users/me/avatar",
            files={"file": ("avatar.png", b"avatar-bytes", "image/png")},
        )

    assert upload.status_code == 200
    assert upload.json()["url"] == f"/api/users/{normal_user.id}/avatar"

    db.refresh(normal_user)
    get_ok = auth_client.get(f"/api/users/{normal_user.id}/avatar")
    assert get_ok.status_code == 200

    Path(normal_user.avatar_path).unlink(missing_ok=True)

    missing_file = auth_client.get(f"/api/users/{normal_user.id}/avatar")
    assert missing_file.status_code == 404
    assert missing_file.json()["detail"] == "Avatar file missing"


def test_get_avatar_not_found_when_user_or_avatar_missing(client, db, normal_user):
    not_found = client.get("/api/users/999999/avatar")
    assert not_found.status_code == 404

    normal_user.avatar_path = None
    db.commit()

    missing_avatar = client.get(f"/api/users/{normal_user.id}/avatar")
    assert missing_avatar.status_code == 404
    assert missing_avatar.json()["detail"] == "Avatar not found"
