from datetime import datetime, timedelta, timezone

from app.models.job import JobStatus, JobType, ScanJob
from app.models.library import Library


def test_active_job_returns_inactive_when_none(client):
    response = client.get("/api/jobs/active")

    assert response.status_code == 200
    assert response.json() == {"active": False}


def test_active_job_returns_cleanup_placeholder_library_name(client, db):
    now = datetime.now(timezone.utc)
    running_job = ScanJob(
        library_id=None,
        job_type=JobType.CLEANUP,
        status=JobStatus.RUNNING,
        started_at=now - timedelta(minutes=2),
        force_scan=False,
    )
    db.add(running_job)
    db.commit()
    db.refresh(running_job)

    response = client.get("/api/jobs/active")

    assert response.status_code == 200
    payload = response.json()
    assert payload["active"] is True
    assert payload["job_id"] == running_job.id
    assert payload["library_name"] == "-"
    assert payload["library_id"] is None


def test_list_jobs_applies_filters_and_parses_summary(admin_client, db):
    library = Library(name="Jobs-Lib", path="/tmp/jobs-lib")
    db.add(library)
    db.flush()

    base_time = datetime.now(timezone.utc)
    completed = ScanJob(
        library_id=library.id,
        job_type=JobType.SCAN,
        status=JobStatus.COMPLETED,
        created_at=base_time - timedelta(minutes=10),
        started_at=base_time - timedelta(minutes=9),
        completed_at=base_time - timedelta(minutes=4),
        result_summary='{"issues_added": 12}',
    )
    deleted_library_job = ScanJob(
        library_id=99999,
        job_type=JobType.THUMBNAIL,
        status=JobStatus.FAILED,
        created_at=base_time - timedelta(minutes=2),
        started_at=base_time - timedelta(minutes=2),
        completed_at=base_time - timedelta(minutes=1),
        error_message="Worker died",
    )
    db.add_all([completed, deleted_library_job])
    db.commit()
    db.refresh(completed)

    filtered = admin_client.get("/api/jobs?status=completed&limit=5")
    assert filtered.status_code == 200
    filtered_payload = filtered.json()
    assert len(filtered_payload) == 1
    assert filtered_payload[0]["id"] == completed.id
    assert filtered_payload[0]["library_name"] == "Jobs-Lib"
    assert filtered_payload[0]["summary"] == {"issues_added": 12}
    assert filtered_payload[0]["duration_seconds"] == 300.0

    unfiltered = admin_client.get("/api/jobs?limit=5")
    assert unfiltered.status_code == 200
    unfiltered_payload = unfiltered.json()
    assert len(unfiltered_payload) == 2
    assert unfiltered_payload[0]["library_name"] == "Deleted Library"
    assert unfiltered_payload[0]["error"] == "Worker died"


def test_get_job_status_success_and_not_found(admin_client, db):
    library = Library(name="Status-Lib", path="/tmp/status-lib")
    db.add(library)
    db.flush()

    job = ScanJob(
        library_id=library.id,
        job_type=JobType.SCAN,
        status=JobStatus.RUNNING,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    response = admin_client.get(f"/api/jobs/status/{job.id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == job.id
    assert payload["status"] == JobStatus.RUNNING
    assert payload["job_type"] == JobType.SCAN

    missing = admin_client.get("/api/jobs/status/999999")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Job not found"}


def test_get_job_details_success_and_not_found(admin_client, db):
    now = datetime.now(timezone.utc)
    job = ScanJob(
        library_id=None,
        job_type=JobType.CLEANUP,
        status=JobStatus.COMPLETED,
        started_at=now - timedelta(minutes=5),
        completed_at=now - timedelta(minutes=1),
        result_summary='{"deleted_files": 4}',
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    response = admin_client.get(f"/api/jobs/{job.id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == job.id
    assert payload["library_name"] == "-"
    assert payload["summary"] == {"deleted_files": 4}
    assert payload["force_scan"] is False

    missing = admin_client.get("/api/jobs/999999")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Job not found"}
