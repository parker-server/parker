def test_home_page_shows_storage_warning_for_admin_when_startup_looks_suspicious(admin_client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.pages.collect_startup_diagnostics",
        lambda db, database_url: {
            "status": "storage_mismatch_suspected",
            "status_title": "Storage Mismatch Suspected",
            "status_summary": "Parker can see comics but the database has no libraries configured.",
            "recommended_actions": ["Verify /app/storage"],
        },
    )

    response = admin_client.get("/")

    assert response.status_code == 200
    body = response.text
    assert "Storage Mismatch Suspected" in body
    assert "Open Diagnostics" in body
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
