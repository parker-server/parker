import xml.etree.ElementTree as ET
from unittest.mock import patch

from app.core.comic_helpers import DOWNLOADS_ENABLED_SETTING, user_can_download
from app.routers.opds import get_opds_download_filename
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.setting import SystemSetting
from app.models.user import User
from app.core.security import get_password_hash
from tests.factories import create_library_with_root


ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
ACQUISITION_REL = "http://opds-spec.org/acquisition"


def _set_bool_setting(db, key: str, value: bool) -> None:
    setting = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if not setting:
        setting = SystemSetting(key=key, value="", category="server", data_type="bool")
        db.add(setting)
    setting.value = "true" if value else "false"
    db.commit()


def _create_downloadable_comic(db, user, tmp_path) -> Comic:
    archive_path = tmp_path / "download-test-001.cbz"
    archive_path.write_bytes(b"fake-zip")

    library = create_library_with_root(db, "Download Permission Library", str(tmp_path))
    series = Series(name="Download Test", library=library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="1",
        title="Grab It",
        filename=archive_path.name,
        library_root_id=library.active_root.id,
        relative_path=archive_path.name,
        file_size=8,
    )
    db.add_all([series, volume, comic])
    user.accessible_libraries.append(library)
    db.commit()
    return comic


# --- Permission helper ---

def test_user_can_download_defaults_to_allowed(db, normal_user):
    assert normal_user.can_download is True
    assert user_can_download(normal_user, db) is True


def test_user_can_download_respects_user_flag(db, normal_user):
    normal_user.can_download = False
    db.commit()

    assert user_can_download(normal_user, db) is False


def test_user_can_download_respects_site_setting(db, normal_user):
    _set_bool_setting(db, DOWNLOADS_ENABLED_SETTING, False)

    assert user_can_download(normal_user, db) is False


def test_admin_can_always_download(db, admin_user):
    admin_user.can_download = False
    _set_bool_setting(db, DOWNLOADS_ENABLED_SETTING, False)

    assert user_can_download(admin_user, db) is True


# --- Web download ---

def test_web_download_returns_original_file(auth_client, db, normal_user, tmp_path):
    comic = _create_downloadable_comic(db, normal_user, tmp_path)

    response = auth_client.get(f"/api/comics/{comic.id}/download")

    assert response.status_code == 200
    assert response.content == b"fake-zip"
    assert response.headers["content-type"].startswith("application/vnd.comicbook+zip")
    assert "Download%20Test%20%231%20-%20Grab%20It.cbz" in response.headers["content-disposition"]


def test_web_download_blocked_for_user_without_permission(auth_client, db, normal_user, tmp_path):
    comic = _create_downloadable_comic(db, normal_user, tmp_path)
    normal_user.can_download = False
    db.commit()

    response = auth_client.get(f"/api/comics/{comic.id}/download")

    assert response.status_code == 403


def test_web_download_blocked_when_site_disabled(auth_client, db, normal_user, tmp_path):
    comic = _create_downloadable_comic(db, normal_user, tmp_path)
    _set_bool_setting(db, DOWNLOADS_ENABLED_SETTING, False)

    response = auth_client.get(f"/api/comics/{comic.id}/download")

    assert response.status_code == 403


def test_web_download_hides_comics_outside_user_libraries(auth_client, db, normal_user, tmp_path):
    other_user = User(
        username="other-owner",
        email="other-owner@example.com",
        hashed_password=get_password_hash("password123"),
    )
    db.add(other_user)
    db.commit()
    comic = _create_downloadable_comic(db, other_user, tmp_path)

    response = auth_client.get(f"/api/comics/{comic.id}/download")

    assert response.status_code == 404


def test_comic_detail_page_shows_download_only_when_allowed(auth_client, db, normal_user, tmp_path):
    comic = _create_downloadable_comic(db, normal_user, tmp_path)

    allowed = auth_client.get(f"/comics/{comic.id}")
    assert allowed.status_code == 200
    assert "data-download-comic" in allowed.text

    normal_user.can_download = False
    db.commit()

    denied = auth_client.get(f"/comics/{comic.id}")
    assert denied.status_code == 200
    assert "data-download-comic" not in denied.text


# --- OPDS ---

def _opds_get(client, user, path):
    with patch("app.api.opds_deps.verify_password", return_value=True):
        return client.get(path, auth=(user.username, "any_password"))


def _acquisition_links(response_text):
    root = ET.fromstring(response_text)
    return root.findall(f"atom:entry/atom:link[@rel='{ACQUISITION_REL}']", ATOM_NS)


def test_opds_download_and_feed_links_follow_permission(client, db, normal_user, tmp_path):
    _set_bool_setting(db, "server.opds_enabled", True)
    comic = _create_downloadable_comic(db, normal_user, tmp_path)

    feed = _opds_get(client, normal_user, f"/opds/volumes/{comic.volume_id}")
    assert feed.status_code == 200
    assert len(_acquisition_links(feed.text)) == 1
    assert _opds_get(client, normal_user, f"/opds/download/{comic.id}").status_code == 200

    normal_user.can_download = False
    db.commit()

    feed = _opds_get(client, normal_user, f"/opds/volumes/{comic.volume_id}")
    assert feed.status_code == 200
    assert _acquisition_links(feed.text) == []
    assert _opds_get(client, normal_user, f"/opds/download/{comic.id}").status_code == 403


def test_opds_download_blocked_when_site_disabled(client, db, normal_user, tmp_path):
    _set_bool_setting(db, "server.opds_enabled", True)
    _set_bool_setting(db, DOWNLOADS_ENABLED_SETTING, False)
    comic = _create_downloadable_comic(db, normal_user, tmp_path)

    assert _opds_get(client, normal_user, f"/opds/download/{comic.id}").status_code == 403


# --- Admin user management ---

def test_admin_can_create_and_update_download_permission(admin_client, db):
    created = admin_client.post(
        "/api/users/",
        json={
            "username": "no-downloads",
            "email": "no-downloads@example.com",
            "password": "password123",
            "can_download": False,
        },
    )
    assert created.status_code == 200
    assert created.json()["can_download"] is False

    user_id = created.json()["id"]
    listed = admin_client.get("/api/users/")
    listed_user = next(u for u in listed.json()["items"] if u["id"] == user_id)
    assert listed_user["can_download"] is False

    updated = admin_client.patch(
        f"/api/users/{user_id}",
        json={"email": "no-downloads@example.com", "can_download": True},
    )
    assert updated.status_code == 200

    user = db.query(User).filter(User.id == user_id).one()
    db.refresh(user)
    assert user.can_download is True


def test_admin_users_page_notes_when_downloads_are_off_site_wide(admin_client, db):
    enabled = admin_client.get("/admin/users")
    assert enabled.status_code == 200
    assert "data-downloads-disabled-note" not in enabled.text

    _set_bool_setting(db, DOWNLOADS_ENABLED_SETTING, False)

    disabled = admin_client.get("/admin/users")
    assert disabled.status_code == 200
    assert disabled.text.count("data-downloads-disabled-note") == 2


# --- Download filenames ---

def _filename_for(series_name, volume_number=1, **comic_kwargs):
    series = Series(name=series_name) if series_name else None
    volume = Volume(series=series, volume_number=volume_number)
    comic_kwargs.setdefault("filename", "source-file.cbz")
    return get_opds_download_filename(Comic(id=42, volume=volume, **comic_kwargs))


def test_download_filename_uses_series_number_and_title():
    assert _filename_for("Amazing Spider-Man", number="15", title="Spider-Man!") == "Amazing Spider-Man #15 - Spider-Man!.cbz"


def test_download_filename_adds_volume_after_the_first():
    assert _filename_for("Batman", volume_number=2, number="1", title=None) == "Batman v2 #1.cbz"


def test_download_filename_falls_back_to_file_name_without_number_or_title():
    assert _filename_for("One Shots", number=None, title=None) == "One Shots - source-file.cbz"


def test_download_filename_keeps_extension_and_removes_unsafe_characters():
    filename = _filename_for("What If?", number="1", title="Who: Me/You", filename="what-if-001.cbr")
    assert filename == "What If_ #1 - Who_ Me_You.cbr"
