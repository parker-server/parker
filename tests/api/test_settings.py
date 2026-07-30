from pathlib import Path
from unittest.mock import patch

from app.core.login_backgrounds import STATIC_COVERS
from app.models.setting import SystemSetting
from app.services.settings_service import (
    SCANNING_BATCH_WINDOW_MIN_SECONDS,
    SERVER_DISPLAY_NAME_MAX_LENGTH,
    SettingsService,
    generate_cover_options,
)
from app.api.deps import get_current_user_optional
from app.main import app


def test_get_public_setting_without_auth_uses_cached_value(client):
    with patch("app.api.settings.get_cached_setting", return_value="solid_color"):
        response = client.get("/api/settings/ui.login_background_style")

    assert response.status_code == 200
    assert response.json() == {"value": "solid_color"}


def test_get_public_setting_falls_back_to_service_when_cache_misses(client):
    with patch("app.api.settings.get_cached_setting", return_value=None), \
         patch("app.api.settings.SettingsService.get", return_value="grid"):
        response = client.get("/api/settings/ui.login_background_style")

    assert response.status_code == 200
    assert response.json() == {"value": "grid"}


def test_get_settings_grouped_list_returns_service_payload(admin_client):
    grouped = {
        "ui": [
            {
                "key": "ui.background_style",
                "value": "solid",
                "category": "ui",
                "data_type": "str",
                "label": "Background Style",
            }
        ]
    }
    with patch("app.api.settings.SettingsService.get_all_grouped", return_value=grouped):
        response = admin_client.get("/api/settings/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ui"][0]["key"] == "ui.background_style"
    assert payload["ui"][0]["value"] == "solid"


def test_get_settings_grouped_list_includes_min_value_metadata(admin_client, db):
    SettingsService(db).initialize_defaults()

    response = admin_client.get("/api/settings/")

    assert response.status_code == 200
    scanning_settings = response.json()["scanning"]
    batch_window = next(
        setting
        for setting in scanning_settings
        if setting["key"] == "scanning.batch_window"
    )
    assert batch_window["min_value"] == SCANNING_BATCH_WINDOW_MIN_SECONDS


def test_login_background_style_options_include_static_cover_cycling(admin_client, db):
    SettingsService(db).initialize_defaults()

    response = admin_client.get("/api/settings/")

    assert response.status_code == 200
    appearance_settings = response.json()["appearance"]
    login_style = next(
        setting
        for setting in appearance_settings
        if setting["key"] == "ui.login_background_style"
    )
    assert {"label": "Cycle Static Covers", "value": "cycling_static_covers"} in login_style["options"]


def test_login_static_cover_default_uses_existing_webp_asset(db):
    SettingsService(db).initialize_defaults()

    setting = db.query(SystemSetting).filter(SystemSetting.key == "ui.login_static_cover").first()

    assert setting.value == "amazing-fantasy-15.webp"


def test_login_static_cover_stale_default_is_normalized_to_webp(db):
    db.add(
        SystemSetting(
            key="ui.login_static_cover",
            value="amazing-fantasy-15.jpg",
            category="appearance",
            data_type="select",
            label="Login Static Cover",
        )
    )
    db.commit()

    SettingsService(db).initialize_defaults()

    setting = db.query(SystemSetting).filter(SystemSetting.key == "ui.login_static_cover").first()
    assert setting.value == "amazing-fantasy-15.webp"


def test_login_static_cover_options_are_alphabetized_by_label():
    options = generate_cover_options()
    labels = [option["label"] for option in options]

    assert labels == sorted(labels, key=str.casefold)


def test_all_bundled_login_cover_assets_are_selectable():
    cover_dir = Path(__file__).resolve().parents[2] / "static" / "img" / "login-covers"
    asset_filenames = {path.name for path in cover_dir.glob("*.webp")}

    assert asset_filenames == set(STATIC_COVERS)


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


def test_update_setting_rejects_server_display_name_that_is_too_long(admin_client, db):
    SettingsService(db).initialize_defaults()

    response = admin_client.patch(
        "/api/settings/general.app_name",
        json={"value": "X" * (SERVER_DISPLAY_NAME_MAX_LENGTH + 1)},
    )

    assert response.status_code == 422
    assert f"{SERVER_DISPLAY_NAME_MAX_LENGTH} characters or fewer" in response.json()["detail"]


def test_update_setting_rejects_scan_batch_window_below_minimum(admin_client, db):
    SettingsService(db).initialize_defaults()

    response = admin_client.patch(
        "/api/settings/scanning.batch_window",
        json={"value": SCANNING_BATCH_WINDOW_MIN_SECONDS - 1},
    )

    assert response.status_code == 422
    assert f"at least {SCANNING_BATCH_WINDOW_MIN_SECONDS} seconds" in response.json()["detail"]


def test_initialize_defaults_seeds_short_server_display_name(db):
    service = SettingsService(db)

    service.initialize_defaults()

    assert service.get("general.app_name") == "Parker"


def test_initialize_defaults_seeds_single_volume_redirect_setting(db):
    service = SettingsService(db)

    service.initialize_defaults()

    setting = db.query(SystemSetting).filter(
        SystemSetting.key == "ui.auto_redirect_single_volume_series"
    ).first()
    assert setting is not None
    assert service.get("ui.auto_redirect_single_volume_series") is False
    assert setting.category == "appearance"
    assert setting.data_type == "bool"


def test_initialize_defaults_clamps_existing_scan_batch_window_below_minimum(db):
    db.add(
        SystemSetting(
            key="scanning.batch_window",
            value=str(SCANNING_BATCH_WINDOW_MIN_SECONDS - 1),
            category="scanning",
            data_type="int",
            label="Old Label",
            description="Old description",
        )
    )
    db.commit()

    service = SettingsService(db)
    service.initialize_defaults()

    setting = db.query(SystemSetting).filter(SystemSetting.key == "scanning.batch_window").first()
    assert setting is not None
    assert service.get("scanning.batch_window") == SCANNING_BATCH_WINDOW_MIN_SECONDS
    assert setting.label == "Scan Batch Window (Sec)"


def test_initialize_defaults_preserves_existing_custom_server_display_name(db):
    db.add(
        SystemSetting(
            key="general.app_name",
            value="Fortress Comics",
            category="general",
            data_type="string",
            label="Old Label",
            description="Old description",
        )
    )
    db.commit()

    service = SettingsService(db)
    service.initialize_defaults()

    setting = db.query(SystemSetting).filter(SystemSetting.key == "general.app_name").first()
    assert setting is not None
    assert setting.value == "Fortress Comics"
    assert setting.label == "Server Display Name"
