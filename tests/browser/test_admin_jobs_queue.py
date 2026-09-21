from datetime import datetime, timedelta, timezone

import pytest

from app.models.job import JobStatus, JobType, ScanJob
from app.models.library import Library
from app.models.user import User


@pytest.mark.browser
def test_admin_jobs_page_shows_running_and_queued_jobs(page, browser_server):
    job_ids = []
    session = browser_server["db_factory"]()
    try:
        user = session.get(User, browser_server["seed"]["user_id"])
        user.is_superuser = True
        library = session.query(Library).filter(Library.name == "Browser Test Library").one()

        now = datetime.now(timezone.utc)
        running = ScanJob(
            library_id=library.id,
            job_type=JobType.SCAN,
            status=JobStatus.RUNNING,
            created_at=now - timedelta(minutes=12),
            started_at=now - timedelta(minutes=10),
        )
        pending = ScanJob(
            library_id=library.id,
            job_type=JobType.THUMBNAIL,
            status=JobStatus.PENDING,
            created_at=now - timedelta(minutes=5),
        )
        session.add_all([running, pending])
        session.commit()
        job_ids = [running.id, pending.id]
    finally:
        session.close()

    try:
        page.goto(f"{browser_server['base_url']}/admin/jobs", wait_until="networkidle")

        queue = page.get_by_test_id("active-job-queue")
        queue.get_by_role("heading", name="Active Queue").wait_for()
        queue.get_by_test_id("active-running-count").get_by_text("1 running").wait_for()
        queue.get_by_test_id("active-pending-count").get_by_text("1 queued").wait_for()
        rows = queue.get_by_test_id("active-job-row")
        running_row = rows.filter(has_text="running").filter(has_text="scan")
        pending_row = rows.filter(has_text="queued").filter(has_text="thumbnail")

        running_row.get_by_role("cell", name="Now").wait_for()
        pending_row.get_by_role("cell", name="#1").wait_for()
        running_row.get_by_text("Browser Test Library").wait_for()
        pending_row.get_by_text("Browser Test Library").wait_for()
        assert rows.count() == 2
    finally:
        session = browser_server["db_factory"]()
        try:
            if job_ids:
                session.query(ScanJob).filter(ScanJob.id.in_(job_ids)).delete(synchronize_session=False)
            user = session.get(User, browser_server["seed"]["user_id"])
            if user is not None:
                user.is_superuser = False
            session.commit()
        finally:
            session.close()
