import logging
import pytest
import xml.etree.ElementTree as ET
from app.services.settings_service import SettingsService
from app.models.setting import SystemSetting
from app.models.library import Library
from app.models.series import Series
from app.models.comic import Comic, Volume


def test_opds_disabled_by_default(client, normal_user):
    """
    Ensure that even with valid credentials, OPDS returns 503
    if the feature is disabled in settings.
    """
    # 1. Ensure setting is False (Default)
    # Note: We don't need to mock SettingsService, we just update the DB via the fixture
    # The fixture 'client' uses the same 'db' session.

    # 2. Try to access root feed with Basic Auth
    response = client.get(
        "/opds/",
        auth=(normal_user.username, "fakehash")  # "fakehash" matches the user fixture
    )

    # 3. Should fail with Service Unavailable (not 401, but 503)
    assert response.status_code == 503
    assert "disabled" in response.json()["detail"]


def test_opds_auth_flow(client, db, normal_user):
    """
    Test the full flow: Enable Setting -> Bad Auth -> Good Auth
    """
    # 1. Enable OPDS
    # We manually update the DB to simulate the Admin toggling it ON
    setting = db.query(SystemSetting).filter(SystemSetting.key == "server.opds_enabled").first()
    if not setting:
        # Create if missing (though initialize_defaults usually handles this)
        setting = SystemSetting(
            key="server.opds_enabled",
            value="true",
            category="server",
            data_type="bool"
        )
        db.add(setting)
    else:
        setting.value = "true"
    db.commit()

    # 2. Try with WRONG password
    response = client.get(
        "/opds/",
        auth=(normal_user.username, "wrong_password")
    )
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers
    assert response.headers["WWW-Authenticate"] == 'Basic realm="Parker OPDS"'

    # 3. Try with CORRECT password (using the fixture's password)
    # Note: The 'normal_user' fixture sets hashed_password="fakehash".
    # In a real app, verify_password checks hash.
    # IN TESTING: We need to make sure verify_password works with our fixture.
    # If your 'verify_password' implementation in 'app/core/security.py' uses bcrypt,
    # 'fakehash' won't work unless we mock verify_password or create a valid hash.

    # For this test to pass with the 'normal_user' fixture as written,
    # we assume we need to patch 'verify_password' to return True.
    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        response = client.get(
            "/opds/",
            auth=(normal_user.username, "any_password")
        )

        assert response.status_code == 200
        assert "application/atom+xml" in response.headers["content-type"]
        assert "<feed" in response.text
        assert "Parker Library" in response.text


def test_opds_logs_invalid_password(client, db, normal_user, caplog):
    _enable_opds(db)
    caplog.set_level(logging.WARNING, logger="app.auth")

    response = client.get(
        "/opds/",
        auth=(normal_user.username, "wrong_password")
    )

    assert response.status_code == 401
    assert any(
        "Authentication failed via OPDS basic auth" in record.message
        and "reason=invalid_password" in record.message
        and f"username='{normal_user.username}'" in record.message
        for record in caplog.records
    )


def test_opds_logs_unknown_user(client, db, caplog):
    _enable_opds(db)
    caplog.set_level(logging.WARNING, logger="app.auth")

    response = client.get(
        "/opds/",
        auth=("missing-user", "wrong_password")
    )

    assert response.status_code == 401
    assert any(
        "Authentication failed via OPDS basic auth" in record.message
        and "reason=unknown_user" in record.message
        and "username='missing-user'" in record.message
        for record in caplog.records
    )


def test_opds_missing_credentials_returns_basic_realm(client, db):
    _enable_opds(db)

    response = client.get("/opds/")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == 'Basic realm="Parker OPDS"'


def _enable_opds(db):
    setting = db.query(SystemSetting).filter(SystemSetting.key == "server.opds_enabled").first()
    if not setting:
        setting = SystemSetting(
            key="server.opds_enabled",
            value="true",
            category="server",
            data_type="bool"
        )
        db.add(setting)
    else:
        setting.value = "true"
    db.commit()


def test_opds_library_feed_renders_series_entries(client, db, normal_user):
    _enable_opds(db)

    library = Library(name="OPDS Library", path="/tmp/opds-library")
    series = Series(name="Alpha Flight", library=library, summary_override="Team book")
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="1",
        title="First Issue",
        filename="alpha-flight-001.cbz",
        file_path="/tmp/alpha-flight-001.cbz",
        updated_at=series.updated_at,
    )
    db.add_all([library, series, volume, comic])
    normal_user.accessible_libraries.append(library)
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        response = client.get(
            f"/opds/libraries/{library.id}",
            auth=(normal_user.username, "any_password")
        )

    assert response.status_code == 200
    root = ET.fromstring(response.text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall("atom:entry", ns)
    assert len(entries) == 1
    assert entries[0].findtext("atom:title", namespaces=ns) == "Alpha Flight"
    subsection = entries[0].find("atom:link[@rel='subsection']", ns)
    assert subsection is not None
    assert subsection.get("href") == f"http://testserver/opds/series/{series.id}"
    assert "Team book" in response.text


def test_opds_series_feed_handles_missing_month_and_day(client, db, normal_user):
    _enable_opds(db)

    library = Library(name="Series Library", path="/tmp/opds-series-library")
    series = Series(name="Beta Ray", library=library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="7",
        title="Stormbreaker",
        filename="beta-ray-007.cbz",
        file_path="/tmp/beta-ray-007.cbz",
        year=2024,
        month=None,
        day=None,
        file_size=12345,
    )
    db.add_all([library, series, volume, comic])
    normal_user.accessible_libraries.append(library)
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        response = client.get(
            f"/opds/series/{series.id}",
            auth=(normal_user.username, "any_password")
        )

    assert response.status_code == 200
    root = ET.fromstring(response.text)
    ns = {"atom": "http://www.w3.org/2005/Atom", "dcterms": "http://purl.org/dc/terms/"}
    entry = root.find("atom:entry", ns)
    assert entry is not None
    assert entry.findtext("dcterms:issued", namespaces=ns) == "2024-01-01"
    acquisition = entry.find("atom:link[@rel='http://opds-spec.org/acquisition']", ns)
    assert acquisition is not None
    acquisitions = entry.findall("atom:link[@rel='http://opds-spec.org/acquisition']", ns)
    assert len(acquisitions) == 1
    assert acquisition.get("type") == "application/vnd.comicbook+zip"
    assert acquisition.get("href", "").startswith("http://testserver/opds/download/")
    assert acquisition.get("href", "").endswith("/Comic%20-%20Stormbreaker.cbz")


def test_opds_download_uses_real_archive_type_and_extension(client, db, normal_user, tmp_path):
    _enable_opds(db)

    archive_path = tmp_path / "gamma-ray-001.cbr"
    archive_path.write_bytes(b"fake-rar")

    library = Library(name="Download Library", path=str(tmp_path / "download-library"))
    series = Series(name="Gamma Ray", library=library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="1",
        title="First Blast",
        filename="gamma-ray-001.cbr",
        file_path=str(archive_path),
    )
    db.add_all([library, series, volume, comic])
    normal_user.accessible_libraries.append(library)
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        response = client.get(
            f"/opds/download/{comic.id}",
            auth=(normal_user.username, "any_password")
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.comicbook-rar")
    assert 'filename="Comic - First Blast.cbr"' in response.headers["content-disposition"]


def test_opds_download_uses_filename_stem_when_title_missing(client, db, normal_user, tmp_path):
    _enable_opds(db)

    archive_path = tmp_path / "titleless-special.cbz"
    archive_path.write_bytes(b"fake-zip")

    library = Library(name="Titleless Library", path=str(tmp_path / "titleless-library"))
    series = Series(name="Titleless Series", library=library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="2",
        title=None,
        filename="titleless-special.cbz",
        file_path=str(archive_path),
    )
    db.add_all([library, series, volume, comic])
    normal_user.accessible_libraries.append(library)
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        response = client.get(
            f"/opds/download/{comic.id}",
            auth=(normal_user.username, "any_password")
        )

    assert response.status_code == 200
    assert 'filename="Comic - titleless-special.cbz"' in response.headers["content-disposition"]
