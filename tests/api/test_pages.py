from pathlib import Path

from app.core.templates import templates
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.services.settings_service import SettingsService
from tests.factories import create_comic, create_library_with_root


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


def test_admin_about_page_exposes_wiki_and_git_commit(admin_client, monkeypatch):
    monkeypatch.setattr("app.routers.admin.get_build_commit_hash", lambda: "abc123def456")

    response = admin_client.get("/admin/about")

    assert response.status_code == 200
    body = response.text
    assert "https://github.com/parker-server/parker/wiki" in body
    assert "Wiki" in body
    assert "Application Version" in body
    assert "Git Commit" in body
    assert "abc123def456" in body


def test_admin_build_commit_hash_prefers_environment(monkeypatch):
    from app.core import build_info

    build_info.get_build_commit_hash.cache_clear()
    monkeypatch.setenv("PARKER_BUILD_COMMIT", "feedface1234")

    try:
        assert build_info.get_build_commit_hash() == "feedface1234"
    finally:
        build_info.get_build_commit_hash.cache_clear()


def test_admin_diagnostics_page_exposes_support_snapshot_actions(admin_client):
    response = admin_client.get("/admin/diagnostics")

    assert response.status_code == 200
    body = response.text
    assert "Copy Support Snapshot" in body
    assert "Download JSON" in body
    assert "Open Raw JSON" in body
    assert "parker_startup_diagnostics" in body
    assert "git_commit_hash" in body
    assert "document.execCommand('copy')" in body


def test_admin_diagnostics_page_renders_configured_library_roots(admin_client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.admin.collect_startup_diagnostics",
        lambda db, database_url: {
            "status": "healthy",
            "status_title": "Healthy",
            "status_summary": "Everything looks good.",
            "is_suspicious": False,
            "recommended_actions": [],
            "runtime": {"mode": "local_filesystem", "label": "Local filesystem"},
            "database": {
                "url": "sqlite:///test.db",
                "path": "test.db",
                "exists": True,
                "size_bytes": 0,
                "size_display": "0 B",
                "wal_size_bytes": None,
                "wal_size_display": None,
                "shm_size_bytes": None,
                "shm_size_display": None,
                "alembic_version": "head",
            },
            "counts": {"users": 1, "libraries": 1, "series": 0, "comics": 0},
            "default_admin_present": True,
            "library_sample": [
                {
                    "name": "Multi Root Library",
                    "path": "C:/Comics/Main",
                    "path_exists": True,
                    "root_count": 3,
                    "active_root_count": 2,
                    "roots": [
                        {"id": 1, "path": "C:/Comics/Main", "is_active": True, "path_exists": True},
                        {"id": 2, "path": "D:/Comics/Archive", "is_active": True, "path_exists": False},
                        {"id": 3, "path": "E:/Comics/Offline", "is_active": False, "path_exists": None},
                    ],
                }
            ],
            "comics_root": {"path": "/comics", "exists": False, "sample": []},
        },
    )

    response = admin_client.get("/admin/diagnostics")

    assert response.status_code == 200
    body = response.text
    assert "Multi Root Library" in body
    assert "Showing all 1 library" in body
    assert "max-h-96" in body
    assert "2 active / 3 roots" in body
    assert "C:/Comics/Main" in body
    assert "D:/Comics/Archive" in body
    assert "E:/Comics/Offline" in body
    assert "Inactive" in body
    assert "Path Found" in body
    assert "Path Missing" in body
    assert "Path Unknown" in body


def test_admin_diagnostics_page_labels_sampled_library_count(admin_client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.admin.collect_startup_diagnostics",
        lambda db, database_url: {
            "status": "healthy",
            "status_title": "Healthy",
            "status_summary": "Everything looks good.",
            "is_suspicious": False,
            "recommended_actions": [],
            "runtime": {"mode": "local_filesystem", "label": "Local filesystem"},
            "database": {
                "url": "sqlite:///test.db",
                "path": "test.db",
                "exists": True,
                "size_bytes": 0,
                "size_display": "0 B",
                "wal_size_bytes": None,
                "wal_size_display": None,
                "shm_size_bytes": None,
                "shm_size_display": None,
                "alembic_version": "head",
            },
            "counts": {"users": 1, "libraries": 12, "series": 0, "comics": 0},
            "default_admin_present": True,
            "library_sample": [
                {"name": f"Library {index}", "path": f"C:/Comics/{index}", "path_exists": True, "roots": []}
                for index in range(1, 6)
            ],
            "comics_root": {"path": "/comics", "exists": False, "sample": []},
        },
    )

    response = admin_client.get("/admin/diagnostics")

    assert response.status_code == 200
    assert "Showing 5 of 12 libraries" in response.text


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


def test_login_page_cycles_static_covers(client, db):
    settings_service = SettingsService(db)
    settings_service.initialize_defaults()
    settings_service.update("ui.login_background_style", "cycling_static_covers")

    response = client.get("/login")

    assert response.status_code == 200
    body = response.text
    assert "cycling_static_covers" in body
    assert "/static/img/login-covers/action-comics-1.webp" in body
    assert "/static/img/login-covers/amazing-fantasy-15.webp" in body
    assert "fetchBackgrounds()" in body
    assert "loadStaticCovers()" in body


def test_login_page_static_cover_fallback_uses_webp_asset(client, db):
    settings_service = SettingsService(db)
    settings_service.initialize_defaults()
    settings_service.update("ui.login_background_style", "static_cover")
    settings_service.update("ui.login_static_cover", "amazing-fantasy-15.jpg")

    response = client.get("/login")

    assert response.status_code == 200
    body = response.text
    assert "/static/img/login-covers/amazing-fantasy-15.webp" in body
    assert "/static/img/login-covers/amazing-fantasy-15.jpg" not in body


def test_admin_settings_page_exposes_quick_navigation(admin_client):
    response = admin_client.get("/admin/settings")

    assert response.status_code == 200
    body = response.text
    assert "Settings Overview" in body
    assert "Jump To" in body
    assert "categorySettingGroups(category)" in body
    assert "Expand All" in body
    assert "Collapse All" in body
    assert 'x-text="setting.key"' not in body


def test_admin_tasks_page_uses_refresh_description_api_route_name(admin_client):
    response = admin_client.get("/admin/tasks")

    assert response.status_code == 200
    body = response.text
    assert "runTask('refresh_descriptions')" in body
    assert "runTask('refresh-descriptions')" not in body
    assert '"refresh_descriptions": "/api/tasks/refresh-descriptions"' in body


def test_admin_libraries_page_exposes_folder_browser_route(admin_client):
    response = admin_client.get("/admin/libraries")

    assert response.status_code == 200
    body = response.text
    assert "Browse" in body
    assert "libraries.browse" in body
    assert "Relocate" in body
    assert "libraries.relocation_preview" in body
    assert "libraries.relocation_confirm" in body
    assert "scan_recommended" in body
    assert "confirm_blocked" in body
    assert "relocationConfirmBlocked" in body
    assert "relocationSampleSummary" in body
    assert "Showing ${shown.toLocaleString()} of ${total.toLocaleString()}" in body
    assert "Scan Recommended" in body
    assert "startScan(scanLibrary, true)" in body
    assert "Watch Library" in body
    assert "Automatically scan active roots when files are added or changed." in body
    assert "Watch Folder" not in body
    assert "Use Roots to relocate or manage this library's paths safely." not in body
    assert "Library Roots" in body
    assert "libraries.roots_create" in body
    assert "libraries.roots_update" in body
    assert "libraries.roots_delete" in body
    assert "Additional roots can be managed after creation with the Roots link." in body
    assert "This initial path becomes the library's first active root." in body
    assert "openRootsModal" in body
    assert 'x-on:click="openRelocateModal(lib)"' not in body
    assert "openRelocateModal(roots.library, root)" in body
    assert "delete_comics=true" in body
    assert "roots.library?.is_scanning || roots.adding" in body
    assert "Disable Last Active Root?" in body
    assert "Existing comics will remain visible and readable if their files are reachable" in body


def test_timestamp_views_use_shared_utc_local_date_helpers(admin_client):
    checks = [
        ("/admin/users", "window.parker.formatLocalDateTime(dateStr)"),
        ("/admin/jobs", "window.parker.formatLocalDateTime(dateStr)"),
        ("/admin/libraries", "window.parker.formatLocalDateTime(dateStr)"),
        ("/admin/cbl-sources", "window.parker.formatLocalDateTime(dateStr)"),
        ("/user/dashboard", "window.parker.formatLocalDate(dateStr"),
        ("/user/following", "window.parker.formatLocalDate(value"),
        ("/continue-reading", "window.parker.formatLocalDate(item.last_read_at)"),
    ]

    for path, expected in checks:
        response = admin_client.get(path)
        assert response.status_code == 200
        assert expected in response.text


def test_shared_datetime_helpers_assume_naive_api_timestamps_are_utc():
    script = Path("static/js/app.js").read_text(encoding="utf-8")

    assert "const parseUtcDate" in script
    assert "normalized = `${trimmed}T00:00:00Z`;" in script
    assert "normalized = `${trimmed.replace(' ', 'T')}Z`;" in script
    assert "formatLocalDateTime" in script
    assert "formatLocalDate" in script


def test_timeline_year_labels_remain_sticky_in_year_and_decade_modes(auth_client):
    response = auth_client.get("/timelines")

    assert response.status_code == 200
    body = response.text
    assert "sticky top-24 text-3xl font-black text-white" in body
    assert "sticky top-24 text-2xl font-black text-white" in body


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


def test_collection_reading_list_and_stack_pages_expose_comic_count_labels(auth_client):
    collections_response = auth_client.get("/collections")
    reading_lists_response = auth_client.get("/reading-lists")
    stacks_response = auth_client.get("/stacks")

    assert collections_response.status_code == 200
    assert "col.comic_count || 0" in collections_response.text

    assert reading_lists_response.status_code == 200
    assert "list.comic_count || 0" in reading_lists_response.text

    assert stacks_response.status_code == 200
    assert "list.comic_count || 0" in stacks_response.text


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


def test_comic_detail_page_gates_file_location_on_file_path(auth_client, db, normal_user):
    library = create_library_with_root(db, "Comic Detail Page Library", "/tmp/comic-detail-page-library")
    series = Series(name="Comic Detail Page Series", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()
    comic = create_comic(
        db,
        volume,
        library.active_root,
        "comic-detail-page-1.cbz",
        number="1",
        title="Comic Detail Page Issue",
        filename="comic-detail-page-1.cbz",
    )

    normal_user.accessible_libraries.append(library)
    db.commit()

    response = auth_client.get(f"/comics/{comic.id}")

    assert response.status_code == 200
    body = response.text
    assert 'x-show="comic?.file_path"' in body
    assert 'x-text="comic?.file_path"' in body


def test_libraries_page_gates_library_path_on_api_payload(auth_client):
    response = auth_client.get("/libraries")

    assert response.status_code == 200
    body = response.text
    assert 'x-show="lib.path"' in body
    assert 'x-text="lib.path"' in body


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
    assert "series.details" in response.text
    assert "series?.details" not in response.text


def test_series_page_keeps_multi_volume_series_when_setting_enabled(admin_client, db, monkeypatch):
    series, _ = _seed_series_page_data(db, volume_count=2)
    monkeypatch.setattr("app.routers.pages.get_cached_setting", lambda key, default=None: True)

    response = admin_client.get(f"/series/{series.id}", follow_redirects=False)

    assert response.status_code == 200
    assert "seriesDetail()" in response.text
    assert "series.details" in response.text
    assert "series?.details" not in response.text


def test_volume_page_series_breadcrumb_uses_series_escape_hatch(admin_client, db):
    _, volumes = _seed_series_page_data(db)

    response = admin_client.get(f"/volumes/{volumes[0].id}")

    assert response.status_code == 200
    body = response.text
    assert "?show_series=1" in body
    assert "recommendationLanes" in body
    assert "loadRecommendationsForSingleVolumeSeries()" in body
    assert "series_volume_count !== 1" in body
    assert "series.recommendations" in body
    assert "volumes.details" in body
    assert "volume?.details" not in body


def test_user_settings_page_renders_for_authenticated_user(auth_client):
    response = auth_client.get("/user/settings")

    assert response.status_code == 200
    assert "Account Settings" in response.text


def test_user_year_in_review_page_renders_for_authenticated_user(auth_client):
    response = auth_client.get("/user/year-in-review")

    assert response.status_code == 200
