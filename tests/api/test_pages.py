from app.core.templates import templates


def test_home_page_shows_storage_warning_for_admin_when_startup_looks_suspicious(admin_client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.pages.collect_startup_diagnostics",
        lambda db, database_url: {
            "status": "storage_mismatch_suspected",
            "status_title": "Storage Mismatch Suspected",
            "status_summary": "Parker can see comics but the database has no libraries configured.",
            "recommended_actions": ["Verify the active storage directory"],
        },
    )

    response = admin_client.get("/")

    assert response.status_code == 200
    body = response.text
    assert "Storage Mismatch Suspected" in body
    assert "Open Diagnostics" in body
    assert "Back to Admin" in body
    assert "Manage Libraries Anyway" in body
    assert "storage_mismatch_suspected" in body


def test_home_page_keeps_normal_onboarding_when_startup_state_is_healthy(admin_client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.pages.collect_startup_diagnostics",
        lambda db, database_url: {
            "status": "healthy",
            "status_title": "Healthy",
            "status_summary": "Healthy",
            "recommended_actions": [],
        },
    )

    response = admin_client.get("/")

    assert response.status_code == 200
    body = response.text
    assert "Welcome to Parker!" in body
    assert "storage_mismatch_suspected" not in body


def test_admin_dashboard_links_to_diagnostics(admin_client):
    response = admin_client.get("/admin")

    assert response.status_code == 200
    body = response.text
    assert "Diagnostics" in body
    assert "Inspect the active database" in body


def test_admin_diagnostics_page_exposes_support_snapshot_actions(admin_client):
    response = admin_client.get("/admin/diagnostics")

    assert response.status_code == 200
    body = response.text
    assert "Copy Support Snapshot" in body
    assert "Download JSON" in body
    assert "Open Raw JSON" in body
    assert "parker_startup_diagnostics" in body
    assert "document.execCommand('copy')" in body


def test_login_page_uses_server_display_name_but_keeps_parker_branding(client, monkeypatch):
    def fake_get_system_setting(key, default=None):
        if key == "general.app_name":
            return "Fortress Comics"
        return default

    monkeypatch.setitem(templates.env.globals, "get_system_setting", fake_get_system_setting)

    response = client.get("/login")

    assert response.status_code == 200
    body = response.text
    assert "Fortress Comics" in body
    assert "Powered by" in body
    assert "Parker" in body


def test_admin_settings_page_exposes_quick_navigation(admin_client):
    response = admin_client.get("/admin/settings")

    assert response.status_code == 200
    body = response.text
    assert "Settings Overview" in body
    assert "Jump To" in body
    assert "Expand All" in body
    assert "Collapse All" in body
    assert 'x-text="setting.key"' not in body


def test_search_widget_people_results_use_generic_creator_handoff(auth_client):
    response = auth_client.get("/")

    assert response.status_code == 200
    body = response.text
    assert 'personSearchHref(item)' in body
    assert 'field=writer&value=${encodeURIComponent(item.name)}&operator=contains' not in body


def test_advanced_search_page_exposes_full_creator_filter_set(auth_client):
    response = auth_client.get("/search")

    assert response.status_code == 200
    body = response.text
    assert '<option value="letterer">Letterer</option>' in body
    assert '<option value="cover_artist">Cover Artist</option>' in body


def test_collection_and_reading_list_pages_expose_comic_count_labels(auth_client):
    collections_response = auth_client.get("/collections")
    reading_lists_response = auth_client.get("/reading-lists")

    assert collections_response.status_code == 200
    assert "col.comic_count || 0" in collections_response.text

    assert reading_lists_response.status_code == 200
    assert "list.comic_count || 0" in reading_lists_response.text


def test_continue_reading_page_exposes_pagination_controls(auth_client):
    response = auth_client.get("/continue-reading")

    assert response.status_code == 200
    body = response.text
    assert "window.parker.paginationMixin(" in body
    assert "'progress.recent_progress'" in body
    assert "mode: 'infinite'" in body
    assert "x-ref=\"loadSentinel\"" in body
    assert "Page <span class=\"text-white font-bold\" x-text=\"page\"></span> of" in body


def test_user_settings_page_renders_for_authenticated_user(auth_client):
    response = auth_client.get("/user/settings")

    assert response.status_code == 200
    assert "Account Settings" in response.text


def test_user_year_in_review_page_renders_for_authenticated_user(auth_client):
    response = auth_client.get("/user/year-in-review")

    assert response.status_code == 200
