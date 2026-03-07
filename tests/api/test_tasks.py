from unittest.mock import patch

from app.models.library import Library


def test_cleanup_task_queues_job(admin_client):
    with patch("app.api.tasks.scan_manager.add_cleanup_task", return_value={"status": "queued", "job_id": 1}):
        response = admin_client.post("/api/tasks/cleanup")

    assert response.status_code == 200
    assert response.json() == {"status": "queued", "job_id": 1}


def test_backup_task_returns_details(admin_client):
    with patch("app.api.tasks.BackupService.create_backup", return_value={"path": "/tmp/backup.sqlite", "size": 1234}):
        response = admin_client.post("/api/tasks/backup")

    assert response.status_code == 200
    assert response.json() == {
        "message": "Backup created successfully",
        "details": {"path": "/tmp/backup.sqlite", "size": 1234},
    }


def test_refresh_descriptions_task_returns_stats(admin_client):
    with patch("app.api.tasks.MaintenanceService.refresh_reading_list_descriptions", return_value={"updated": 3, "skipped": 1}):
        response = admin_client.post("/api/tasks/refresh-descriptions")

    assert response.status_code == 200
    assert response.json() == {
        "message": "Enrichment complete",
        "stats": {"updated": 3, "skipped": 1},
    }


def test_refresh_colorscapes_counts_only_queued_libraries(admin_client, db):
    db.add_all([
        Library(name="Colors-1", path="/tmp/colors-1"),
        Library(name="Colors-2", path="/tmp/colors-2"),
    ])
    db.commit()

    with patch("app.api.tasks.scan_manager.add_thumbnail_task", side_effect=[{"status": "queued"}, {"status": "skipped"}]):
        response = admin_client.post("/api/tasks/refresh-colorscapes")

    assert response.status_code == 200
    assert response.json() == {
        "message": "Queued background processing for 1 libraries.",
        "stats": {"libraries_queued": 1},
    }
