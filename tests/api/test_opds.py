import pytest
from app.services.settings_service import SettingsService
from app.models.setting import SystemSetting


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
    assert response.headers["WWW-Authenticate"] == "Basic"

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