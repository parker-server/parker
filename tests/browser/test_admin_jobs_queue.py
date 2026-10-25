import json
from datetime import datetime, timedelta, timezone

import pytest

from app.models.comic import Comic
from app.models.job import JobStatus, JobType, ScanJob
from app.models.library import Library
from app.models.user import User
from app.services.external_reviews import issue_display_title


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


@pytest.mark.browser
def test_admin_jobs_page_shows_review_lookups_separately(page, browser_server):
    job_ids = []
    session = browser_server["db_factory"]()
    try:
        user = session.get(User, browser_server["seed"]["user_id"])
        user.is_superuser = True
        comic = session.get(Comic, browser_server["seed"]["completed_comic_id"])
        comic_label = issue_display_title(comic.volume.series.name, comic.number)

        now = datetime.now(timezone.utc)
        pending_lookup = ScanJob(
            job_type=JobType.EXTERNAL_REVIEW_LOOKUP,
            target_type="comic",
            target_id=comic.id,
            payload_json=json.dumps({"provider": "comicbookroundup"}),
            status=JobStatus.PENDING,
            created_at=now - timedelta(minutes=1),
        )
        finished_lookup = ScanJob(
            job_type=JobType.EXTERNAL_REVIEW_LOOKUP,
            target_type="comic",
            target_id=comic.id,
            payload_json=json.dumps({"provider": "comicbookroundup"}),
            status=JobStatus.COMPLETED,
            created_at=now - timedelta(minutes=30),
            started_at=now - timedelta(minutes=30),
            completed_at=now - timedelta(minutes=29),
            result_summary=json.dumps({
                "provider": "comicbookroundup",
                "comic_id": comic.id,
                "issue_title": comic_label,
                "match_status": "no_reviews",
                "confidence_score": 91.5,
                "review_count": 0,
                "source_issue_url": "https://comicbookroundup.com/comic-books/reviews/test/1",
                "error": None,
            }),
        )
        session.add_all([pending_lookup, finished_lookup])
        session.commit()
        job_ids = [pending_lookup.id, finished_lookup.id]
    finally:
        session.close()

    try:
        page.goto(f"{browser_server['base_url']}/admin/jobs", wait_until="networkidle")

        # Queued lookup shows in the active queue with its own lane and the issue as target
        queue = page.get_by_test_id("active-job-queue")
        lookup_row = queue.get_by_test_id("active-job-row").filter(has_text="review lookup")
        lookup_row.get_by_text("enrichment lane").wait_for()
        lookup_row.get_by_text(comic_label).wait_for()

        # Finished lookups are hidden from history until the toggle is turned on
        history_lookup = page.locator("tbody tr").filter(has_text=f"#{job_ids[1]}")
        assert history_lookup.count() == 0

        page.get_by_test_id("show-enrichment-jobs").check()
        history_lookup.get_by_text("no critic reviews").wait_for()
        history_lookup.get_by_text(comic_label).wait_for()

        history_lookup.get_by_role("button", name="Details").click()
        details = page.get_by_test_id("review-lookup-details")
        details.wait_for()
        details.get_by_text("ComicBookRoundup", exact=True).wait_for()
        details.get_by_role("link", name="https://comicbookroundup.com/comic-books/reviews/test/1").wait_for()

        # Long values ("no critic reviews", the provider name) must wrap inside their boxes
        stats = details.get_by_test_id("review-stat")
        assert stats.count() == 3
        for viewport in ({"width": 1280, "height": 800}, {"width": 375, "height": 800}):
            page.set_viewport_size(viewport)
            overflowing = stats.evaluate_all(
                "boxes => boxes.filter(box => box.scrollWidth > box.clientWidth).map(box => box.innerText)"
            )
            assert overflowing == [], f"stat boxes overflow at {viewport['width']}px: {overflowing}"
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
