from unittest.mock import patch

from app.api.deps import get_current_user_optional
from app.main import app


def test_get_public_setting_without_auth_uses_cached_value(client):
    with patch("app.api.settings.get_cached_setting", return_value="solid_color"):
        response = client.get("/api/settings/ui.login_background_style")

    assert response.status_code == 200
    assert response.json() == {"value": "solid_color"}


def test_get_protected_setting_requires_auth(client):
    response = client.get("/api/settings/server.opds_enabled")

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_get_protected_setting_non_admin_returns_null(client, normal_user):
    app.dependency_overrides[get_current_user_optional] = lambda: normal_user

    response = client.get("/api/settings/server.opds_enabled")

    assert response.status_code == 200
    assert response.json() is None


def test_get_protected_setting_admin_returns_value(client, admin_user):
    app.dependency_overrides[get_current_user_optional] = lambda: admin_user

    with patch("app.api.settings.SettingsService.get", return_value=True):
        response = client.get("/api/settings/server.opds_enabled")

    assert response.status_code == 200
    assert response.json() == {"value": True}


def test_update_setting_triggers_scheduler_reschedule_for_task_intervals(admin_client):
    with patch("app.api.settings.SettingsService.update", return_value={"key": "system.task.backup.interval", "value": "daily"}), \
         patch("app.api.settings.scheduler_service.reschedule_jobs") as mock_reschedule:
        response = admin_client.patch("/api/settings/system.task.backup.interval", json={"value": "daily"})

    assert response.status_code == 200
    assert response.json() == {"key": "system.task.backup.interval", "value": "daily"}
    mock_reschedule.assert_called_once()


def test_update_setting_returns_404_when_setting_missing(admin_client):
    with patch("app.api.settings.SettingsService.update", side_effect=ValueError("missing")):
        response = admin_client.patch("/api/settings/system.task.unknown.interval", json={"value": "daily"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Setting not found"
