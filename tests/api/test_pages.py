from app.core.templates import templates
from app.models.comic import Comic, Volume
from app.models.series import Series
from tests.factories import create_library_with_root


def _seed_series_page_data(db, volume_count=1):
    library = create_library_with_root(db, f"Page Test Library {volume_count}", f"/tmp/page-test-library-{volume_count}")
    root = library.active_root
    series = Series(name=f"Page Test Series {volume_count}", library=library)
    db.add(series)

    volumes = []
    for index in range(1, volume_count + 1):
        volume = Volume(series=series, volume_number=index)
        volumes.append(volume)
        db.add(
            Comic(
                volume=volume,
                number="1",
                filename=f"page-test-{volume_count}-{index}.cbz",
                library_root_id=root.id,
                relative_path=f"page-test-{volume_count}-{index}.cbz",
                page_count=10,
            )
        )

    db.commit()
    db.refresh(series)
    for volume in volumes:
        db.refresh(volume)

    return series, volumes


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


def test_admin_libraries_page_exposes_folder_browser_route(admin_client):
    response = admin_client.get("/admin/libraries")

    assert response.status_code == 200
    body = response.text
    assert "Browse" in body
    assert "libraries.browse" in body
    assert "The path is locked for existing libraries" in body


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


def test_reader_page_uses_modular_reader_shell(auth_client):
    response = auth_client.get("/reader/123")

    assert response.status_code == 200
    body = response.text
    assert "window.createReader({ comicId: 123 })" in body
    assert "/static/js/reader.js" in body
    assert 'x-on:click="toggleViewMode()"' in body


def test_series_page_redirects_to_single_volume_when_setting_enabled(admin_client, db, monkeypatch):
    series, volumes = _seed_series_page_data(db)
    monkeypatch.setattr("app.routers.pages.get_cached_setting", lambda key, default=None: True)

    response = admin_client.get(f"/series/{series.id}", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].endswith(f"/volumes/{volumes[0].id}")


def test_series_page_show_series_query_skips_single_volume_redirect(admin_client, db, monkeypatch):
    series, _ = _seed_series_page_data(db)
    monkeypatch.setattr("app.routers.pages.get_cached_setting", lambda key, default=None: True)

    response = admin_client.get(f"/series/{series.id}?show_series=1", follow_redirects=False)

    assert response.status_code == 200
    assert "seriesDetail()" in response.text


def test_series_page_keeps_multi_volume_series_when_setting_enabled(admin_client, db, monkeypatch):
    series, _ = _seed_series_page_data(db, volume_count=2)
    monkeypatch.setattr("app.routers.pages.get_cached_setting", lambda key, default=None: True)

    response = admin_client.get(f"/series/{series.id}", follow_redirects=False)

    assert response.status_code == 200
    assert "seriesDetail()" in response.text


def test_volume_page_series_breadcrumb_uses_series_escape_hatch(admin_client, db):
    _, volumes = _seed_series_page_data(db)

    response = admin_client.get(f"/volumes/{volumes[0].id}")

    assert response.status_code == 200
    assert "?show_series=1" in response.text


def test_user_settings_page_renders_for_authenticated_user(auth_client):
    response = auth_client.get("/user/settings")

    assert response.status_code == 200
    assert "Account Settings" in response.text


def test_user_year_in_review_page_renders_for_authenticated_user(auth_client):
    response = auth_client.get("/user/year-in-review")

    assert response.status_code == 200
