import threading
import time
import json
import traceback
import logging
from datetime import datetime, timezone
from sqlalchemy import asc

from app.core.settings_loader import get_system_setting, get_cached_setting
from app.database import SessionLocal
# Import from the package 'app.models' to trigger __init__.py
from app.models import ScanJob, Library
from app.models.job import JobType, JobStatus

from app.services.scanner import LibraryScanner
from app.services.maintenance import MaintenanceService
from app.services.thumbnailer import ThumbnailService


class ScanManager:
    _instance = None


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

        # 1. RECOVERY: Check for jobs interrupted by a crash
        self._recover_interrupted_jobs()

        # 2. Start the DB polling worker
        self.worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.worker_thread.start()

        self._initialized = True

    def _recover_interrupted_jobs(self):
        """Mark jobs that were 'RUNNING' during startup as FAILED"""
        db = SessionLocal()
        try:
            stuck_jobs = db.query(ScanJob).filter(ScanJob.status == JobStatus.RUNNING).all()
            if stuck_jobs:
                print(f"Recovering {len(stuck_jobs)} interrupted scan jobs...")
                self.logger.info(f"Recovering {len(stuck_jobs)} interrupted scan jobs...")

                for job in stuck_jobs:
                    job.status = JobStatus.FAILED
                    job.error_message = "Scan interrupted by server restart"
                    job.completed_at = datetime.now(timezone.utc)

                    # Also reset the library flag
                    if job.library:
                        job.library.is_scanning = False
                db.commit()
        except Exception as e:
            print(f"Error during job recovery: {e}")
        finally:
            db.close()

    def add_task(self, library_id: int, force: bool = False) -> dict:
        """Create a new job record in the database"""
        db = SessionLocal()
        try:
            # CHANGED: Only block if there is already a PENDING job.
            # If a job is RUNNING, we allow adding ONE pending job to the queue
            # so it runs immediately after the current one finishes.
            # Note: We check for SCAN jobs specifically to avoid blocking thumbnails
            existing = db.query(ScanJob).filter(
                ScanJob.library_id == library_id,
                ScanJob.job_type == JobType.SCAN,
                ScanJob.status == JobStatus.PENDING
            ).first()

            if existing:
                return {
                    "status": "ignored",
                    "job_id": existing.id,
                    "message": f"Scan already exists in state: {existing.status}"
                }

            job = ScanJob(
                library_id=library_id,
                force_scan=force,
                job_type=JobType.SCAN,
                status=JobStatus.PENDING
            )
            db.add(job)
            db.commit()
            db.refresh(job)

            return {
                "status": "queued",
                "job_id": job.id,
                "message": "Scan job added to database"
            }
        finally:
            db.close()

    def get_status(self):
        """Get status of the active or next job"""
        db = SessionLocal()
        try:
            active_job = db.query(ScanJob).filter(ScanJob.status == JobStatus.RUNNING).first()
            pending_count = db.query(ScanJob).filter(ScanJob.status == JobStatus.PENDING).count()

            return {
                "is_scanning": bool(active_job),
                "current_job_id": active_job.id if active_job else None,
                "current_library_id": active_job.library_id if active_job else None,
                "current_job_type": active_job.job_type if active_job else None,
                "pending_jobs": pending_count
            }
        finally:
            db.close()

    def _process_queue(self):
        """Poller loop: Check DB for pending jobs"""

        self.logger.info("Database Job Worker Started")

        while not self._stop_event.is_set():
            db = SessionLocal()

            try:
                # 1. High Priority: SCANS
                job = db.query(ScanJob).filter(
                    ScanJob.status == JobStatus.PENDING,
                    ScanJob.job_type == JobType.SCAN
                ).order_by(asc(ScanJob.created_at)).first()

                # 2. Medium Priority: THUMBNAILS
                if not job:
                    job = db.query(ScanJob).filter(
                        ScanJob.status == JobStatus.PENDING,
                        ScanJob.job_type == JobType.THUMBNAIL
                    ).order_by(asc(ScanJob.created_at)).first()

                # 3. Low Priority: CLEANUP
                if not job:
                    job = db.query(ScanJob).filter(
                        ScanJob.status == JobStatus.PENDING,
                        ScanJob.job_type == JobType.CLEANUP
                    ).order_by(asc(ScanJob.created_at)).first()


                if job:
                    # Lock the job: Mark as RUNNING immediately
                    job.status = JobStatus.RUNNING
                    job.started_at = datetime.now(timezone.utc)

                    # Also set the library flag for UI
                    if job.library:
                        job.library.is_scanning = True

                    db.commit()

                    # Extract primitive data to pass to worker
                    # We MUST do this because 'job' object is bound to 'db' session
                    # which we are about to close.
                    job_id = job.id
                    library_id = job.library_id
                    job_type = job.job_type
                    force_scan = job.force_scan

                    db.close()  # Close polling session

                    # Execute in fresh session
                    if job_type == JobType.SCAN:
                        self._run_scan_job(job_id, library_id, force_scan)
                    elif job_type == JobType.THUMBNAIL:
                        self._run_thumbnail_job(job_id, library_id, force_scan)
                    elif job_type == JobType.CLEANUP:
                        self._run_cleanup_job(job_id, library_id)

                    # Don't sleep if we just did work, check for next immediately
                    continue

                else:
                    # No jobs? Sleep a bit to save CPU
                    db.close()
                    time.sleep(2)

            except Exception as e:

                self.logger.error(f"Worker polling error: {e}")

                if db:
                    db.close()
                time.sleep(5)

    def _run_scan_job(self, job_id: int, library_id: int, force: bool):
        """Execute the scan logic"""
        db = SessionLocal()
        try:
            job = db.query(ScanJob).get(job_id)
            library = db.query(Library).get(library_id)

            if not library or not job:
                self.logger.error(f"Critical: Job {job_id} or Library missing.")
                return

            # Capture "First Scan" state BEFORE running the scanner (which updates last_scanned)
            is_first_scan = library.last_scanned is None

            self.logger.info(f"Starting SCAN job {job_id} for {library.name} (Forced: {force}) (First scan: {is_first_scan})")

            # --- RUN SCANNER ---
            scanner = LibraryScanner(library, db)
            results = scanner.scan(force=force)
            # -------------------

            # Update Job on Success
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now(timezone.utc)

            # Store summary (exclude the full 'comics' list if it's huge)
            summary = {
                "imported": results.get("imported", 0),
                "updated": results.get("updated", 0),
                "deleted": results.get("deleted", 0),
                "errors": results.get("errors", 0),
                "elapsed": results.get("elapsed", 0)
            }
            job.result_summary = json.dumps(summary)

            # Reset flag momentarily (next jobs will set it back to True if they run immediately)
            library.is_scanning = False

            # --- QUEUE NEXT JOBS ---

            # Create Thumbnail Job
            self.logger.info(f"Scan complete. Queuing thumbnail generation for Library {library_id}")

            thumb_job = ScanJob(
                library_id=library_id,
                job_type=JobType.THUMBNAIL,
                force_scan=force,
                status=JobStatus.PENDING
            )
            db.add(thumb_job)

            # Cleanup Job (Only if NOT first scan)
            if not is_first_scan:
                self.logger.info(f"Queuing cleanup job for Library {library_id} (Not initial scan)")
                cleanup_job = ScanJob(
                    library_id=library_id,
                    job_type=JobType.CLEANUP,
                    status=JobStatus.PENDING
                )
                db.add(cleanup_job)
            else:
                self.logger.info(f"Skipping cleanup job for Library {library_id} (First Scan)")

            db.commit()

        except Exception as e:

            print(f"Scan Job {job_id} Failed: {e}")
            self.logger.error(f"Scan Job {job_id} Failed: {e}")

            traceback.print_exc()
            db.rollback()

            # Re-fetch for error state update
            job = db.query(ScanJob).get(job_id)
            library = db.query(Library).get(library_id)

            if job:
                job.status = JobStatus.FAILED
                job.error_message = str(e)
                job.completed_at = datetime.now(timezone.utc)

            if library:
                library.is_scanning = False

            db.commit()
        finally:
            db.close()

    def _run_thumbnail_job(self, job_id: int, library_id: int, force: bool):
        """Execute the thumbnail logic"""
        db = SessionLocal()
        try:
            job = db.query(ScanJob).get(job_id)
            # We don't necessarily need the library object for logic, just ID

            if not job:
                return

            self.logger.info(f"Starting THUMBNAIL job {job_id} for Library {library_id}")

            # --- RUN THUMBNAILER ---
            service = ThumbnailService(db, library_id)
            use_parallel = get_cached_setting('system.parallel_image_processing', False)
            if use_parallel:
                self.logger.info(f"Using parallel mode for THUMBNAIL job {job_id}")
                stats = service.process_missing_thumbnails_parallel(force=force)
                pass
            else:
                stats = service.process_missing_thumbnails(force=force)
            # -----------------------

            job.status = JobStatus.COMPLETED
            job.result_summary = json.dumps(stats)
            job.completed_at = datetime.now(timezone.utc)

            # --- Reset the Library Flag ---
            library = db.query(Library).get(library_id)
            if library:
                library.is_scanning = False

            db.commit()

        except Exception as e:
            self.logger.error(f"Thumbnail Job {job_id} Failed: {e}")
            traceback.print_exc()
            db.rollback()

            # Re-fetch and Reset Library Flag on Failure ---
            library = db.query(Library).get(library_id)
            if library:
                library.is_scanning = False

            job = db.query(ScanJob).get(job_id)
            if job:
                job.status = JobStatus.FAILED
                job.error_message = str(e)
                job.completed_at = datetime.now(timezone.utc)
            db.commit()
        finally:
            db.close()

    def _run_cleanup_job(self, job_id: int, library_id: int = None):
        """Execute the orphan cleanup logic"""
        db = SessionLocal()
        try:
            job = db.query(ScanJob).get(job_id)
            if not job: return

            # Logging context
            context_str = f"Library {library_id}" if library_id else "GLOBAL"
            self.logger.info(f"Starting CLEANUP job {job_id} ({context_str})")

            # --- RUN MAINTENANCE ---
            maintenance = MaintenanceService(db)

            # Pass library_id directly.
            # If None, MaintenanceService cleans EVERYTHING.
            # If Int, MaintenanceService cleans ONLY that library.
            stats = maintenance.cleanup_orphans(library_id=library_id)
            # -----------------------

            job.status = JobStatus.COMPLETED
            job.result_summary = json.dumps(stats)
            job.completed_at = datetime.now(timezone.utc)

            # Reset Library Flag (Only if it was a scoped job)
            if library_id:
                library = db.query(Library).get(library_id)
                if library:
                    library.is_scanning = False

            db.commit()

        except Exception as e:
            self.logger.error(f"Cleanup Job {job_id} Failed: {e}")
            traceback.print_exc()
            db.rollback()

            # Error State
            if library_id:
                library = db.query(Library).get(library_id)
                if library: library.is_scanning = False

            job = db.query(ScanJob).get(job_id)
            if job:
                job.status = JobStatus.FAILED
                job.error_message = str(e)
                job.completed_at = datetime.now(timezone.utc)
            db.commit()
        finally:
            db.close()

    def add_cleanup_task(self) -> dict:
        """Queue a global cleanup task"""
        db = SessionLocal()
        try:
            # Check for existing pending cleanup to avoid stacking
            existing = db.query(ScanJob).filter(
                ScanJob.job_type == JobType.CLEANUP,
                ScanJob.status == JobStatus.PENDING
            ).first()

            if existing:
                return {"status": "ignored", "job_id": existing.id, "message": "Cleanup already queued"}

            job = ScanJob(
                library_id=None,
                job_type=JobType.CLEANUP,
                status=JobStatus.PENDING
            )
            db.add(job)
            db.commit()
            db.refresh(job)

            return {"status": "queued", "job_id": job.id, "message": "Global cleanup job queued"}
        finally:
            db.close()


# Global instance
scan_manager = ScanManager()