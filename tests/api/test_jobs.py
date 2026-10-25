import json
from datetime import datetime, timedelta, timezone

from app.models.comic import Volume
from app.models.job import JobStatus, JobType, ScanJob
from app.models.series import Series
from tests.factories import create_comic, create_library_with_root


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
    library = create_library_with_root(db, "Jobs-Lib", "/tmp/jobs-lib")

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


def test_job_queue_lists_running_then_pending_jobs_in_worker_order(admin_client, db):
    library = create_library_with_root(db, "Queue-Lib", "/tmp/queue-lib")

    base_time = datetime.now(timezone.utc)
    running = ScanJob(
        library_id=None,
        job_type=JobType.CLEANUP,
        status=JobStatus.RUNNING,
        created_at=base_time - timedelta(minutes=30),
        started_at=base_time - timedelta(minutes=10),
    )
    pending_metadata = ScanJob(
        library_id=library.id,
        job_type=JobType.METADATA_REHYDRATE,
        status=JobStatus.PENDING,
        created_at=base_time - timedelta(minutes=25),
    )
    pending_thumbnail = ScanJob(
        library_id=library.id,
        job_type=JobType.THUMBNAIL,
        status=JobStatus.PENDING,
        created_at=base_time - timedelta(minutes=20),
    )
    pending_scan = ScanJob(
        library_id=library.id,
        job_type=JobType.SCAN,
        status=JobStatus.PENDING,
        created_at=base_time - timedelta(minutes=5),
    )
    completed = ScanJob(
        library_id=library.id,
        job_type=JobType.SCAN,
        status=JobStatus.COMPLETED,
        created_at=base_time - timedelta(minutes=1),
    )
    db.add_all([running, pending_metadata, pending_thumbnail, pending_scan, completed])
    db.commit()

    response = admin_client.get("/api/jobs/queue")

    assert response.status_code == 200
    payload = response.json()
    assert payload["active"] is True
    assert payload["counts"] == {"running": 1, "pending": 3}
    assert [job["id"] for job in payload["running"]] == [running.id]
    assert [job["id"] for job in payload["pending"]] == [
        pending_scan.id,
        pending_thumbnail.id,
        pending_metadata.id,
    ]
    assert [job["queue_position"] for job in payload["pending"]] == [1, 2, 3]
    assert [job["id"] for job in payload["jobs"]] == [
        running.id,
        pending_scan.id,
        pending_thumbnail.id,
        pending_metadata.id,
    ]
    assert payload["running"][0]["library_name"] == "-"
    assert payload["pending"][0]["library_name"] == "Queue-Lib"


def test_get_job_status_success_and_not_found(admin_client, db):
    library = create_library_with_root(db, "Status-Lib", "/tmp/status-lib")

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


# --- External review lookup jobs (enrichment lane) ---

def _create_review_comic(db, *, lib_name: str = "Review-Lib", series_name: str = "Saga", number: str = "1"):
    library = create_library_with_root(db, lib_name, f"/tmp/{lib_name.lower()}")
    series = Series(name=series_name, library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()
    filename = f"{series_name}-{number}.cbz"
    comic = create_comic(db, volume, library.active_root, filename, number=number, filename=filename)
    return library, comic


def _review_job(comic_id: int, status: JobStatus, **kwargs) -> ScanJob:
    return ScanJob(
        job_type=JobType.EXTERNAL_REVIEW_LOOKUP,
        target_type="comic",
        target_id=comic_id,
        payload_json=json.dumps({"provider": "comicbookroundup"}),
        status=status,
        **kwargs,
    )


def test_active_job_ignores_running_review_lookups(client, db):
    library, comic = _create_review_comic(db)
    now = datetime.now(timezone.utc)
    review = _review_job(comic.id, JobStatus.RUNNING, started_at=now - timedelta(minutes=5))
    db.add(review)
    db.commit()

    # A review lookup alone must not light up the "scanning" indicator
    assert client.get("/api/jobs/active").json() == {"active": False}

    scan = ScanJob(
        library_id=library.id,
        job_type=JobType.SCAN,
        status=JobStatus.RUNNING,
        started_at=now - timedelta(minutes=1),
    )
    db.add(scan)
    db.commit()

    payload = client.get("/api/jobs/active").json()
    assert payload["active"] is True
    assert payload["job_id"] == scan.id
    assert payload["library_name"] == "Review-Lib"


def test_list_jobs_hides_review_lookups_unless_requested(admin_client, db):
    library, comic = _create_review_comic(db)
    base_time = datetime.now(timezone.utc)
    scan = ScanJob(
        library_id=library.id,
        job_type=JobType.SCAN,
        status=JobStatus.COMPLETED,
        created_at=base_time - timedelta(minutes=10),
    )
    review = _review_job(
        comic.id,
        JobStatus.FAILED,
        created_at=base_time - timedelta(minutes=1),
        result_summary=json.dumps({"match_status": "failed", "issue_title": "Saga #1"}),
        error_message="timed out",
    )
    db.add_all([scan, review])
    db.commit()

    default_ids = [job["id"] for job in admin_client.get("/api/jobs?limit=10").json()]
    assert default_ids == [scan.id]

    payload = admin_client.get("/api/jobs?limit=10&include_enrichment=true").json()
    assert [job["id"] for job in payload] == [review.id, scan.id]

    review_payload = payload[0]
    assert review_payload["job_type"] == "external_review_lookup"
    assert review_payload["lane"] == "enrichment"
    assert review_payload["library_name"] == "-"
    assert review_payload["target_type"] == "comic"
    assert review_payload["target_id"] == comic.id
    assert review_payload["target_label"] == "Saga #1"
    assert review_payload["error"] == "timed out"

    scan_payload = payload[1]
    assert scan_payload["lane"] == "library"
    assert scan_payload["target_label"] is None
    assert scan_payload["library_name"] == "Review-Lib"


def test_job_queue_numbers_positions_per_lane(admin_client, db):
    library, comic = _create_review_comic(db)
    _, other_comic = _create_review_comic(db, lib_name="Review-Lib-2", series_name="Paper Girls", number="3")
    base_time = datetime.now(timezone.utc)
    pending_review = _review_job(comic.id, JobStatus.PENDING, created_at=base_time - timedelta(minutes=30))
    running_review = _review_job(
        other_comic.id,
        JobStatus.RUNNING,
        created_at=base_time - timedelta(minutes=40),
        started_at=base_time - timedelta(minutes=1),
    )
    pending_scan = ScanJob(
        library_id=library.id,
        job_type=JobType.SCAN,
        status=JobStatus.PENDING,
        created_at=base_time - timedelta(minutes=5),
    )
    pending_thumbnail = ScanJob(
        library_id=library.id,
        job_type=JobType.THUMBNAIL,
        status=JobStatus.PENDING,
        created_at=base_time - timedelta(minutes=4),
    )
    db.add_all([pending_review, running_review, pending_scan, pending_thumbnail])
    db.commit()

    payload = admin_client.get("/api/jobs/queue").json()

    assert payload["counts"] == {"running": 1, "pending": 3}
    assert payload["running"][0]["target_label"] == "Paper Girls #3"

    pending = {job["id"]: job for job in payload["pending"]}
    # Library jobs and review lookups each have their own worker, so each lane starts at #1
    assert pending[pending_scan.id]["queue_position"] == 1
    assert pending[pending_thumbnail.id]["queue_position"] == 2
    assert pending[pending_review.id]["queue_position"] == 1
    assert pending[pending_review.id]["lane"] == "enrichment"
    assert pending[pending_review.id]["target_label"] == "Saga #1"


def test_review_lookup_label_falls_back_when_comic_is_deleted(admin_client, db):
    review = _review_job(
        999999,
        JobStatus.COMPLETED,
        result_summary=json.dumps({"match_status": "no_match", "issue_title": "Old Series #2"}),
    )
    unknown = _review_job(999998, JobStatus.FAILED)
    db.add_all([review, unknown])
    db.commit()

    payload = admin_client.get("/api/jobs?include_enrichment=true").json()
    labels = {job["id"]: job["target_label"] for job in payload}

    assert labels[review.id] == "Old Series #2"
    assert labels[unknown.id] == "Deleted Comic"
