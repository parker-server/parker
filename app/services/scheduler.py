import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services.backup import BackupService
from app.services.maintenance import MaintenanceService
from app.services.scan_manager import scan_manager
from app.models.setting import SystemSetting
from app.models.library import Library

logger = logging.getLogger(__name__)


class SchedulerService:
    _instance = None
    _scheduler = None

    # TASK REGISTRY
    # Centralized configuration for all system tasks.
    # To add a new task, just add an entry here and a default in settings_service.py
    _TASK_REGISTRY = {
        "backup": {
            "func": "run_backup_job",  # Name of the static method below
            "default_interval": "weekly",
            "default_hour": 2,  # 2 AM
            "description": "System Backup"
        },
        "cleanup": {
            "func": "run_cleanup_job",
            "default_interval": "monthly",
            "default_hour": 3,  # 3 AM
            "description": "Orphan Cleanup"
        },
        "scan": {
            "func": "run_scan_job",
            "default_interval": "daily",
            "default_hour": 4,  # 4 AM (when no one is reading)
            "description": "Library Scan"
        }
    }

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SchedulerService, cls).__new__(cls)
            cls._scheduler = BackgroundScheduler()
        return cls._instance

    def start(self):
        """Start the scheduler if not already running."""
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("Scheduler started.")
            self.reschedule_jobs()

    def stop(self):
        """Shutdown the scheduler."""
        if self._scheduler.running:
            self._scheduler.shutdown()
            logger.info("Scheduler stopped.")

    def _get_setting_value(self, key: str, default: str) -> str:
        """Helper to get a setting value from a fresh session."""
        session: Session = SessionLocal()
        try:
            setting = session.query(SystemSetting).filter(SystemSetting.key == key).first()
            return setting.value if setting else default
        finally:
            session.close()

    def reschedule_jobs(self):
        """
        Dynamically configures jobs based on the registry and DB settings.
        """
        self._scheduler.remove_all_jobs()
        logger.info("Rescheduling system tasks...")

        for job_id, config in self._TASK_REGISTRY.items():
            # 1. Construct the convention-based key
            setting_key = f"system.task.{job_id}.interval"

            # 2. Fetch value (or default)
            interval = self._get_setting_value(setting_key, config["default_interval"])

            if interval == "disabled":
                continue

            # 3. Get the actual function method
            job_func = getattr(self, config["func"])

            # 4. Schedule
            trigger = self._get_trigger_for_interval(interval, hour=config["default_hour"])

            self._scheduler.add_job(
                job_func,
                trigger=trigger,
                id=job_id,
                replace_existing=True
            )
            logger.info(f"Scheduled {config['description']}: {interval} (at {config['default_hour']}:00)")

    @staticmethod
    def _get_trigger_for_interval(interval: str, hour: int) -> CronTrigger:
        """
        Map simple string settings to CronTriggers.
        """
        if interval == "daily":
            return CronTrigger(hour=hour, minute=0)
        elif interval == "weekly":
            return CronTrigger(day_of_week='mon', hour=hour, minute=0)
        elif interval == "monthly":
            return CronTrigger(day=1, hour=hour, minute=0)
        else:
            # Default fallback (Weekly)
            return CronTrigger(day_of_week='mon', hour=hour, minute=0)

    # --- JOB WRAPPERS ---
    # These must be static or instance methods that create their own DB session

    @staticmethod
    def run_backup_job():
        logger.info("Running Scheduled Backup...")
        try:
            result = BackupService.create_backup()
            logger.info(f"Backup Complete: {result['filename']}")
        except Exception as e:
            logger.error(f"Backup Failed: {e}")

    @staticmethod
    def run_cleanup_job():
        """
        OPTIMIZED: Delegates to ScanManager to ensure thread safety.
        Does not run directly, queues a job.
        """
        logger.info("Running Scheduled Cleanup...")
        try:
            # We call the manager, which handles DB locking, queuing, and daisy-chaining.
            result = scan_manager.add_cleanup_task()

            if result['status'] == 'queued':
                logger.info(f"Cleanup Job Queued: ID {result['job_id']}")
            else:
                logger.info(f"Cleanup Skipped: {result['message']}")

        except Exception as e:
            logger.error(f"Failed to queue cleanup: {e}")


    @staticmethod
    def run_scan_job():
        logger.info("Running Scheduled Library Scan...")
        session = SessionLocal()
        try:
            libraries = session.query(Library).all()
            if not libraries:
                logger.info("No libraries to scan.")
                return

            for lib in libraries:
                logger.info(f"Queuing scheduled scan for: {lib.name}")
                # We use force=False so it skips unmodified files (fast)
                scan_manager.add_task(lib.id, force=False)

        except Exception as e:
            logger.error(f"Scheduled Scan Failed: {e}")
        finally:
            session.close()

# Singleton accessor
scheduler_service = SchedulerService()