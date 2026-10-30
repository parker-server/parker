import threading
import time
import json
import logging
from datetime import datetime, timezone
from sqlalchemy import asc
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.settings_loader import get_cached_setting
from app.database import SessionLocal, refresh_query_planner_statistics
from app.models import ScanJob, Library
from app.models.job import JobType, JobStatus, JOB_LANES, LIBRARY_LANE

from app.services.scanner import LibraryScanner
from app.services.maintenance import MaintenanceService
from app.services.thumbnailer import ThumbnailService
from app.services.metadata import rehydrate_library_metadata_from_cache
from app.services.external_reviews import (
    COMICBOOKROUNDUP_PROVIDER,
    EXTERNAL_REVIEWS_ENABLED_SETTING,
    STATUS_FAILED as LOOKUP_STATUS_FAILED,
    refresh_external_reviews_for_comic,
)

DEFAULT_INTERRUPTED_MESSAGE = "Scan interrupted by server restart"
INTERRUPTED_MESSAGES = {
    JobType.EXTERNAL_REVIEW_LOOKUP.value: "External review lookup interrupted by server restart",
}


class ScanManager:
    _instance = None

    # Minimum gap between outbound external review lookups. Only the
    # lock-owning process runs the enrichment lane, so this is global.
    EXTERNAL_REVIEW_MIN_INTERVAL_SECONDS = 3.0

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ScanManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.logger = logging.getLogger(__name__)

        self._stop_event = threading.Event()
        self.worker_threads: dict[str, threading.Thread] = {}
        self._last_external_review_at: float | None = None

        self._initialized = True

    def start(self):
        """Start one queue worker per lane in the process that owns the manager lock."""
        if any(thread.is_alive() for thread in self.worker_threads.values()):
            self.logger.info("Scan manager workers already running")
            return

        self._stop_event.clear()
        self._recover_interrupted_jobs()

        self.worker_threads = {}
        for lane in JOB_LANES:
            thread = threading.Thread(
                target=self._process_lane,
                args=(lane,),
                name=f"job-worker-{lane}",
                daemon=True,
            )
            thread.start()
            self.worker_threads[lane] = thread

    def stop(self, timeout: float = 5.0):
        """Ask the queue workers to stop and wait briefly for each to exit."""
        self._stop_event.set()

        for lane, thread in list(self.worker_threads.items()):
            if thread.is_alive():
                thread.join(timeout=timeout)

            if thread.is_alive():
                self.logger.warning(f"Scan manager '{lane}' worker did not stop before timeout")
                continue

            del self.worker_threads[lane]

    def _recover_interrupted_jobs(self):
        """Mark jobs that were 'RUNNING' during startup as FAILED"""
        db = SessionLocal()
        try:
            stuck_jobs = db.query(ScanJob).filter(ScanJob.status == JobStatus.RUNNING).all()
            if stuck_jobs:
                self.logger.info(f"Recovering {len(stuck_jobs)} interrupted scan jobs...")

                for job in stuck_jobs:
                    job.status = JobStatus.FAILED
                    job_type = getattr(job.job_type, "value", job.job_type)
                    job.error_message = INTERRUPTED_MESSAGES.get(job_type, DEFAULT_INTERRUPTED_MESSAGE)
                    job.completed_at = datetime.now(timezone.utc)
                    # Reset library flag directly here
                    if job.library:
                        job.library.is_scanning = False
                db.commit()
        except Exception as e:
            self.logger.error(f"Error during job recovery: {e}")
        finally:
            db.close()

    def _set_library_scanning_status(self, library_id: int, is_scanning: bool):
        """Helper: Update library status with retry logic (Isolated Transaction)"""
        if not library_id: return

        for attempt in range(5):
            db = SessionLocal()
            try:
                db.query(Library).filter(Library.id == library_id).update({"is_scanning": is_scanning})
                db.commit()
                return
            except OperationalError as e:
                if "locked" in str(e).lower() and attempt < 4:
                    time.sleep(0.5)
                    continue
                self.logger.error(f"Failed to set library {library_id} status: {e}")
            except Exception as e:
                self.logger.error(f"Error setting library status: {e}")
            finally:
                db.close()

    def _safe_job_update(self, job_id: int, status: JobStatus, summary: dict = None, error: str = None):
        """Updates job status with RETRY logic."""
        for attempt in range(5):
            db = SessionLocal()
            try:
                job = db.get(ScanJob, job_id)
                if not job: return

                job.status = status
                job.completed_at = datetime.now(timezone.utc)

                if summary:
                    job.result_summary = json.dumps(summary)
                if error:
                    job.error_message = error

                db.commit()
                self.logger.info(f"Job {job_id} updated successfully")
                return
            except OperationalError as e:
                if "locked" in str(e).lower() and attempt < 4:
                    self.logger.warning(f"DB Locked during job #{job_id} update (attempt {attempt + 1}/5). Retrying...")
                    time.sleep(1.0)  # Wait a full second for WAL checkpoint to finish
                    continue
                self.logger.error(f"Failed to update job {job_id}: {e}")
            except Exception as e:
                self.logger.error(f"Critical error updating job {job_id}: {e}")
            finally:
                db.close()

    def add_task(self, library_id: int, force: bool = False) -> dict:
        """Create a new job record"""

        self.logger.debug(f"Adding SCAN job for library {library_id} to queue (force: {force})")


        db = SessionLocal()
        try:
            # STRICT BLOCKING
            existing = db.query(ScanJob).filter(
                ScanJob.library_id == library_id,
                ScanJob.job_type == JobType.SCAN,
                ScanJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING])
            ).first()

            if existing:
                return {"status": "ignored", "job_id": existing.id, "message": "Scan active"}

            job = ScanJob(
                library_id=library_id,
                force_scan=force,
                job_type=JobType.SCAN,
                status=JobStatus.PENDING
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            return {"status": "queued", "job_id": job.id, "message": "Scan queued"}
        finally:
            db.close()

    def add_metadata_rehydrate_task(self, library_id: int) -> dict:
        """Queue a metadata rehydrate job for a library."""
        self.logger.debug(f"Adding METADATA_REHYDRATE job for library {library_id} to queue")

        db = SessionLocal()
        try:
            existing = db.query(ScanJob).filter(
                ScanJob.library_id == library_id,
                ScanJob.job_type == JobType.METADATA_REHYDRATE,
                ScanJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING])
            ).first()

            if existing:
                return {"status": "ignored", "job_id": existing.id, "message": "Metadata rehydrate active"}

            job = ScanJob(
                library_id=library_id,
                job_type=JobType.METADATA_REHYDRATE,
                status=JobStatus.PENDING
            )
            db.add(job)
            db.commit()
            db.refresh(job)

            return {"status": "queued", "job_id": job.id, "message": "Metadata rehydrate queued"}
        finally:
            db.close()

    def add_external_review_task(self, comic_id: int, provider: str = COMICBOOKROUNDUP_PROVIDER) -> dict:
        """
        Queue an external review lookup for a comic.
        A partial unique index allows only one active lookup per comic, so a
        concurrent request from another Uvicorn worker gets the existing job.
        """
        self.logger.debug(f"Adding EXTERNAL_REVIEW_LOOKUP job for comic {comic_id} to queue ({provider})")

        db = SessionLocal()
        try:
            job = ScanJob(
                job_type=JobType.EXTERNAL_REVIEW_LOOKUP,
                target_type="comic",
                target_id=comic_id,
                payload_json=json.dumps({"provider": provider}),
                status=JobStatus.PENDING,
            )
            db.add(job)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                existing = db.query(ScanJob).filter(
                    ScanJob.job_type == JobType.EXTERNAL_REVIEW_LOOKUP,
                    ScanJob.target_type == "comic",
                    ScanJob.target_id == comic_id,
                    ScanJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING])
                ).first()
                return {
                    "status": "ignored",
                    "job_id": existing.id if existing else None,
                    "message": "External review lookup already active",
                }

            return {"status": "queued", "job_id": job.id, "message": "External review lookup queued"}
        finally:
            db.close()

    def _process_lane(self, lane: str):
        """Poller loop for one lane. Claims that lane's job types in priority order."""
        self.logger.info(f"Database Job Worker Started (lane: {lane})")
        job_types = JOB_LANES[lane]
        loops = 0

        while not self._stop_event.is_set():
            db = SessionLocal()
            try:
                # Priority follows the lane's job type order
                job = None
                for job_type in job_types:
                    job = db.query(ScanJob).filter(
                        ScanJob.status == JobStatus.PENDING,
                        ScanJob.job_type == job_type
                    ).order_by(asc(ScanJob.created_at)).first()
                    if job:
                        break

                if job:
                    # ATOMIC CLAIM
                    rows_affected = db.query(ScanJob).filter(
                        ScanJob.id == job.id,
                        ScanJob.status == JobStatus.PENDING
                    ).update({"status": JobStatus.RUNNING, "started_at": datetime.now(timezone.utc)})

                    db.commit()

                    if rows_affected == 0:
                        db.close()
                        continue

                    # Extract data
                    job_data = {
                        "id": job.id,
                        "library_id": job.library_id,
                        "type": job.job_type,
                        "force": job.force_scan,
                        "target_id": job.target_id,
                        "payload": job.payload_json,
                    }
                    db.close()  # Close immediately

                    # Set Flag
                    if job_data['library_id']:
                        self._set_library_scanning_status(job_data['library_id'], True)

                    # Execute
                    if job_data['type'] == JobType.SCAN:
                        self._run_scan_job(job_data)
                    elif job_data['type'] == JobType.THUMBNAIL:
                        self._run_thumbnail_job(job_data)
                    elif job_data['type'] == JobType.CLEANUP:
                        self._run_cleanup_job(job_data)
                    elif job_data['type'] == JobType.METADATA_REHYDRATE:
                        self._run_metadata_rehydrate_job(job_data)
                    elif job_data['type'] == JobType.EXTERNAL_REVIEW_LOOKUP:
                        self._run_external_review_job(job_data)

                else:
                    db.close()
                    # Periodic integrity check (library flags belong to the library lane)
                    if lane == LIBRARY_LANE:
                        loops += 1
                        if loops >= 15:
                            self._fix_stuck_libraries()
                            loops = 0
                    time.sleep(2)

            except Exception as e:
                self.logger.error(f"Worker polling error: {e}")
                if db: db.close()
                time.sleep(5)

    def _fix_stuck_libraries(self):
        """Reset stuck 'is_scanning' flags"""
        db = SessionLocal()
        try:
            scanning_libs = db.query(Library).filter(Library.is_scanning == True).all()
            for lib in scanning_libs:
                active_job = db.query(ScanJob).filter(
                    ScanJob.library_id == lib.id,
                    ScanJob.status == JobStatus.RUNNING
                ).first()
                if not active_job:
                    self.logger.warning(f"Integrity Check: Resetting stuck library '{lib.name}'")
                    lib.is_scanning = False
            db.commit()
        except Exception:
            pass
        finally:
            db.close()

    def _run_scan_job(self, job_data):
        job_id = job_data['id']
        library_id = job_data['library_id']
        force = job_data['force']

        results = {}
        error = None

        # 1. Run Logic
        db_scan = SessionLocal()
        try:
            library = db_scan.get(Library, library_id)
            if library:
                self.logger.info(f"Starting SCAN job {job_id}")
                scanner = LibraryScanner(library, db_scan)

                use_parallel = get_cached_setting("system.parallel_metadata_processing", False)

                self.logger.info(f"Parallel metadata processing is set to {use_parallel}")

                # UNIFIED LOGIC:
                # If Parallel is ON: Let the service auto-detect worker count (0)
                # If Parallel is OFF: Force exactly 1 worker
                workers = 0 if use_parallel else 1

                results = scanner.scan_parallel(force=force, worker_limit=workers)

                if not results.get("fatal_error"):
                    ScanManager.update_library_last_scanned(library_id)

            else:
                error = "Library not found"
        except Exception as e:
            error = str(e)
            self.logger.error(f"Scan failed: {e}", exc_info=True)
        finally:
            db_scan.close()

        # 2. Update Status (With Retry)
        if error:
            self._safe_job_update(job_id, JobStatus.FAILED, error=error)
            self._set_library_scanning_status(library_id, False)
        else:
            summary = {
                "imported": results.get("imported", 0),
                "updated": results.get("updated", 0),
                "deleted": results.get("deleted", 0),
                "skipped": results.get("skipped", 0),
                "errors": results.get("errors", 0),
                "error_details": results.get("error_details", []),
                "elapsed": results.get("elapsed", 0)
            }
            # A writer failure leaves the scan incomplete: report it as FAILED, but keep
            # the counts for what was committed before the failure.
            fatal_error = results.get("fatal_error")
            if fatal_error:
                self.logger.error(f"SCAN job {job_id} failed in the metadata writer: {fatal_error}")

            self._safe_job_update(
                job_id,
                JobStatus.FAILED if fatal_error else JobStatus.COMPLETED,
                summary=summary,
                error=fatal_error,
            )
            # Comics committed before a writer failure still changed the data.
            self._refresh_database_statistics()

            # 3. Queue Pipeline: THUMBNAIL -> CLEANUP
            # We queue both now so they run in sequence via priority
            # (also after a writer failure: comics committed before it still need thumbnails)
            db_queue = SessionLocal()
            try:
                # Add Thumbnail Job
                db_queue.add(ScanJob(
                    library_id=library_id,
                    job_type=JobType.THUMBNAIL,
                    force_scan=force,
                    status=JobStatus.PENDING
                ))
                # Add Cleanup Job
                db_queue.add(ScanJob(
                    library_id=library_id,
                    job_type=JobType.CLEANUP,
                    status=JobStatus.PENDING
                ))
                db_queue.commit()
            except Exception as e:
                self.logger.error(f"Failed to queue thumbnail job: {e}")
            finally:
                db_queue.close()

            # NOTE: We do NOT reset the library flag here because the Thumbnail job starts immediately.

    def _run_thumbnail_job(self, job_data):
        job_id = job_data['id']
        library_id = job_data['library_id']
        force = job_data['force']

        stats = {}
        error = None

        # 1. Run Logic
        db_thumb = SessionLocal()
        try:
            self.logger.info(f"Starting THUMBNAIL job {job_id}")

            service = ThumbnailService(db_thumb, library_id)
            use_parallel = get_cached_setting('system.parallel_image_processing', False)

            self.logger.info(f"Parallel image processing is set to {use_parallel}")

            # UNIFIED LOGIC:
            # If Parallel is ON: Let the service auto-detect worker count (0)
            # If Parallel is OFF: Force exactly 1 worker
            workers = 0 if use_parallel else 1

            stats = service.process_missing_thumbnails_parallel(force=force, worker_limit=workers)

        except Exception as e:
            error = str(e)
            self.logger.error(f"Thumbnail failed: {e}", exc_info=True)
        finally:
            db_thumb.close()

        # 2. Update Status (With Retry)
        if error:
            self._safe_job_update(job_id, JobStatus.FAILED, error=error)
        else:
            self._safe_job_update(job_id, JobStatus.COMPLETED, summary=stats)
            # DECOUPLED: We no longer queue Cleanup here.
            # It is either queued by Scan already, or not needed (manual run).

        # 3. Reset Flag (CRITICAL)
        # Since we don't know if a Cleanup job follows, we must reset the flag.
        # If a Cleanup job IS pending, it will simply set the flag back to True when it starts.
        if library_id:
            self._set_library_scanning_status(library_id, False)

    def _run_cleanup_job(self, job_data):
        job_id = job_data['id']
        library_id = job_data['library_id']

        stats = {}
        error = None

        # 1. Run Logic
        db_clean = SessionLocal()
        try:
            scope_name = f"Library {library_id}" if library_id else "GLOBAL"
            self.logger.info(f"Starting CLEANUP job {job_id} ({scope_name})")

            maintenance = MaintenanceService(db_clean)
            old_jobs_removed = 0
            if library_id is None:
                old_jobs_removed = maintenance.cleanup_old_jobs()

            # Pass 1: Remove DB records for files that no longer exist (e.g., your old CBRs)
            removed_ids = maintenance.cleanup_missing_files(library_id=library_id)

            # 2. Immediately delete those specific thumbnails
            if removed_ids:
                maintenance.delete_thumbnails_by_id(removed_ids)

            self.logger.info(f"Janitor: Removed {len(removed_ids)} dead records from DB.")

            # Pass 2: Delete orphaned Series/Volumes/Tags
            stats = maintenance.cleanup_orphans(library_id=library_id)
            stats["missing_files_removed"] = len(removed_ids)
            if library_id is None:
                stats["old_jobs"] = old_jobs_removed

            # Pass 3: Physical Thumbnail Purge (Only on Global Cleanups)
            # Walking the entire thumb directory is expensive, so we keep it to global runs.
            if library_id is None:
                thumb_stats = maintenance.cleanup_orphaned_thumbnails()
                stats["orphaned_thumbnails_deleted"] = thumb_stats
                stats["orphaned_cover_overrides_deleted"] = maintenance.cleanup_orphaned_cover_overrides()

        except Exception as e:
            error = str(e)
            self.logger.error(f"Cleanup failed: {e}")
        finally:
            db_clean.close()

        # 2. Update Status
        if error:
            self._safe_job_update(job_id, JobStatus.FAILED, error=error)
        else:
            self._safe_job_update(job_id, JobStatus.COMPLETED, summary=stats)
            self._refresh_database_statistics()

        # 3. Reset Flag (Final Step)
        if library_id:
            self._set_library_scanning_status(library_id, False)

    def _run_metadata_rehydrate_job(self, job_data):
        job_id = job_data['id']
        library_id = job_data['library_id']

        summary = {}
        error = None

        db_rehydrate = SessionLocal()
        try:
            library = db_rehydrate.get(Library, library_id)
            if not library:
                error = "Library not found"
            else:
                self.logger.info(f"Starting METADATA_REHYDRATE job {job_id}")
                summary = rehydrate_library_metadata_from_cache(
                    db=db_rehydrate,
                    library_id=library_id,
                    rehydrate_reading_lists=bool(getattr(library, "parse_reading_lists", True)),
                    rehydrate_collections=bool(getattr(library, "parse_collections", True)),
                    rehydrate_story_arcs=bool(getattr(library, "parse_story_arcs", True)),
                )
        except Exception as e:
            error = str(e)
            self.logger.error(f"Metadata rehydrate failed: {e}", exc_info=True)
        finally:
            db_rehydrate.close()

        if error:
            self._safe_job_update(job_id, JobStatus.FAILED, error=error)
        else:
            self._safe_job_update(job_id, JobStatus.COMPLETED, summary=summary)
            self._refresh_database_statistics()

        if library_id:
            self._set_library_scanning_status(library_id, False)

    def _refresh_database_statistics(self):
        """Refresh SQLite's query planner statistics after a job that changed library data."""
        try:
            with SessionLocal() as db:
                refresh_query_planner_statistics(db)
        except Exception as e:
            self.logger.warning(f"Could not refresh database statistics: {e}")

    def _run_external_review_job(self, job_data):
        job_id = job_data['id']
        comic_id = job_data['target_id']
        payload = json.loads(job_data['payload']) if job_data.get('payload') else {}

        summary = {"provider": payload.get("provider", COMICBOOKROUNDUP_PROVIDER), "comic_id": comic_id}
        error = None

        try:
            if not get_cached_setting(EXTERNAL_REVIEWS_ENABLED_SETTING, False):
                summary["skipped"] = "External reviews are disabled"
            else:
                self._wait_for_external_review_interval()
                self.logger.info(f"Starting EXTERNAL_REVIEW_LOOKUP job {job_id} (comic {comic_id})")
                try:
                    result = refresh_external_reviews_for_comic(comic_id)
                finally:
                    self._last_external_review_at = time.monotonic()

                if result is None:
                    summary["skipped"] = "Comic not found"
                else:
                    summary.update(result)
                    # Lookup outcomes like no_match are normal results; only a
                    # network or parsing failure marks the job itself as failed.
                    if result["match_status"] == LOOKUP_STATUS_FAILED:
                        error = result.get("error") or "External review lookup failed"
        except Exception as e:
            error = str(e)
            self.logger.error(f"External review lookup failed: {e}", exc_info=True)

        self._safe_job_update(
            job_id,
            JobStatus.FAILED if error else JobStatus.COMPLETED,
            summary=summary,
            error=error,
        )

    def _wait_for_external_review_interval(self):
        """Keep outbound lookups at least EXTERNAL_REVIEW_MIN_INTERVAL_SECONDS apart."""
        if self._last_external_review_at is None:
            return

        elapsed = time.monotonic() - self._last_external_review_at
        remaining = self.EXTERNAL_REVIEW_MIN_INTERVAL_SECONDS - elapsed
        if remaining > 0:
            # Waits on the stop event so shutdown is not held up.
            self._stop_event.wait(remaining)

    def add_cleanup_task(self) -> dict:
        """Queue a global cleanup task"""

        self.logger.debug(f"Adding CLEANUP job to queue")

        db = SessionLocal()
        try:
            # Check for existing pending cleanup to avoid stacking
            existing = db.query(ScanJob).filter(
                ScanJob.job_type == JobType.CLEANUP,
                ScanJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING])
            ).first()

            if existing:
                return {"status": "ignored", "job_id": existing.id, "message": "Cleanup already queued"}

            job = ScanJob(library_id=None, job_type=JobType.CLEANUP, status=JobStatus.PENDING)
            db.add(job)
            db.commit()
            db.refresh(job)

            return {"status": "queued", "job_id": job.id, "message": "Global cleanup job queued"}
        finally:
            db.close()


    def add_thumbnail_task(self, library_id: int, force: bool = False) -> dict:
        """
        Queue a thumbnail/colorscape generation task.
        This reuses the parallel image processor to backfill missing data.
        """

        self.logger.debug(f"Adding THUMBNAIL job for library {library_id} to queue (force: {force})")

        db = SessionLocal()
        try:
            # Check for existing job to avoid stacking
            existing = db.query(ScanJob).filter(
                ScanJob.library_id == library_id,
                ScanJob.job_type == JobType.THUMBNAIL,
                ScanJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING])
            ).first()

            if existing:
                return {"status": "ignored", "job_id": existing.id, "message": "Job already active"}

            job = ScanJob(
                library_id=library_id,
                force_scan=force,
                job_type=JobType.THUMBNAIL,
                status=JobStatus.PENDING
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            return {"status": "queued", "job_id": job.id, "message": "Job queued"}
        finally:
            db.close()

    @staticmethod
    def update_library_last_scanned(library_id: int):
        db = SessionLocal()
        lib = db.get(Library, library_id)
        if lib:
            lib.last_scanned = datetime.now(timezone.utc)
            db.commit()
        db.close()



# Global instance
scan_manager = ScanManager()
