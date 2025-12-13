import threading
import time
import json
import traceback
import logging
from datetime import datetime, timezone
from sqlalchemy import asc
from sqlalchemy.exc import OperationalError

from app.core.settings_loader import get_cached_setting
from app.database import SessionLocal
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

        # 1. RECOVERY
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
                self.logger.info(f"Recovering {len(stuck_jobs)} interrupted scan jobs...")

                for job in stuck_jobs:
                    job.status = JobStatus.FAILED
                    job.error_message = "Scan interrupted by server restart"
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
                job = db.query(ScanJob).get(job_id)
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

    def _process_queue(self):
        """Poller loop"""
        self.logger.info("Database Job Worker Started")
        loops = 0

        while not self._stop_event.is_set():
            db = SessionLocal()
            try:
                # Priority: SCAN -> THUMBNAIL -> CLEANUP
                job = db.query(ScanJob).filter(
                    ScanJob.status == JobStatus.PENDING,
                    ScanJob.job_type == JobType.SCAN
                ).order_by(asc(ScanJob.created_at)).first()

                if not job:
                    job = db.query(ScanJob).filter(
                        ScanJob.status == JobStatus.PENDING,
                        ScanJob.job_type == JobType.THUMBNAIL
                    ).order_by(asc(ScanJob.created_at)).first()

                if not job:
                    job = db.query(ScanJob).filter(
                        ScanJob.status == JobStatus.PENDING,
                        ScanJob.job_type == JobType.CLEANUP
                    ).order_by(asc(ScanJob.created_at)).first()

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
                        "force": job.force_scan
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

                else:
                    db.close()
                    # Periodic integrity check
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
            library = db_scan.query(Library).get(library_id)
            if library:
                self.logger.info(f"Starting SCAN job {job_id}")
                scanner = LibraryScanner(library, db_scan)
                results = scanner.scan(force=force)
            else:
                error = "Library not found"
        except Exception as e:
            error = str(e)
            self.logger.error(f"Scan failed: {e}")
            traceback.print_exc()
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
                "errors": results.get("errors", 0),
                "elapsed": results.get("elapsed", 0)
            }
            self._safe_job_update(job_id, JobStatus.COMPLETED, summary=summary)

            # 3. Queue Next Job: THUMBNAIL (Cleanup queued AFTER Thumbnail now)
            db_queue = SessionLocal()
            try:
                db_queue.add(ScanJob(
                    library_id=library_id,
                    job_type=JobType.THUMBNAIL,
                    force_scan=force,
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

            if use_parallel:
                stats = service.process_missing_thumbnails_parallel(force=force)
            else:
                stats = service.process_missing_thumbnails(force=force)
        except Exception as e:
            error = str(e)
            self.logger.error(f"Thumbnail failed: {e}")
            traceback.print_exc()
        finally:
            db_thumb.close()

        # 2. Update Status (With Retry)
        if error:
            self._safe_job_update(job_id, JobStatus.FAILED, error=error)
            self._set_library_scanning_status(library_id, False)
        else:
            self._safe_job_update(job_id, JobStatus.COMPLETED, summary=stats)

            # 3. Queue CLEANUP Job (Only if successful)
            # This ensures Strict Order: Scan -> Thumb -> Cleanup
            db_queue = SessionLocal()
            try:
                db_queue.add(ScanJob(
                    library_id=library_id,
                    job_type=JobType.CLEANUP,
                    status=JobStatus.PENDING
                ))
                db_queue.commit()
            except Exception as e:
                # If queuing cleanup fails, we must reset the flag here to avoid sticking
                self.logger.error(f"Failed to queue cleanup job: {e}")
                self._set_library_scanning_status(library_id, False)
            finally:
                db_queue.close()

    def _run_cleanup_job(self, job_data):
        job_id = job_data['id']
        library_id = job_data['library_id']

        stats = {}
        error = None

        # 1. Run Logic
        db_clean = SessionLocal()
        try:
            self.logger.info(f"Starting CLEANUP job {job_id}")
            maintenance = MaintenanceService(db_clean)
            stats = maintenance.cleanup_orphans(library_id=library_id)
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

        # 3. Reset Flag (Final Step)
        if library_id:
            self._set_library_scanning_status(library_id, False)

    def add_cleanup_task(self) -> dict:
        """Queue a global cleanup task"""
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


# Global instance
scan_manager = ScanManager()