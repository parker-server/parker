from types import SimpleNamespace
from unittest.mock import MagicMock

import app.services.scheduler as scheduler
from app.models.setting import SystemSetting


def _service():
    svc = object.__new__(scheduler.SchedulerService)
    svc._scheduler = MagicMock()
    return svc


def _session_local_factory(db):
    bind = db.get_bind()

    def _factory():
        return db.__class__(bind=bind)

    return _factory


def test_scheduler_singleton_new_initializes_once(monkeypatch):
    monkeypatch.setattr(scheduler.SchedulerService, "_instance", None)
    monkeypatch.setattr(scheduler.SchedulerService, "_scheduler", None)

    sched_obj = MagicMock()
    bg_ctor = MagicMock(return_value=sched_obj)
    monkeypatch.setattr(scheduler, "BackgroundScheduler", bg_ctor)

    first = scheduler.SchedulerService()
    second = scheduler.SchedulerService()

    assert first is second
    assert scheduler.SchedulerService._scheduler is sched_obj
    bg_ctor.assert_called_once()


def test_start_only_runs_when_not_running():
    svc = _service()
    svc._scheduler.running = False
    svc._scheduler.start.side_effect = lambda: setattr(svc._scheduler, "running", True)
    svc.reschedule_jobs = MagicMock()

    svc.start()
    svc.start()

    svc._scheduler.start.assert_called_once()
    svc.reschedule_jobs.assert_called_once()


def test_stop_only_runs_when_running():
    svc = _service()
    svc._scheduler.running = True

    svc.stop()
    svc._scheduler.running = False
    svc.stop()

    svc._scheduler.shutdown.assert_called_once()


def test_get_setting_value_reads_existing_and_falls_back(monkeypatch, db):
    svc = _service()
    monkeypatch.setattr(scheduler, "SessionLocal", _session_local_factory(db))

    db.add(
        SystemSetting(
            key="system.task.scan.interval",
            value="daily",
            category="system",
            data_type="string",
            label="Scan",
        )
    )
    db.commit()

    assert svc._get_setting_value("system.task.scan.interval", "weekly") == "daily"
    assert svc._get_setting_value("system.task.cleanup.interval", "monthly") == "monthly"


def test_get_setting_value_always_closes_session(monkeypatch):
    svc = _service()
    fake_db = MagicMock()
    q = fake_db.query.return_value
    q.filter.return_value = q
    q.first.return_value = None
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: fake_db)

    assert svc._get_setting_value("missing", "default") == "default"
    fake_db.close.assert_called_once()


def test_reschedule_jobs_removes_existing_and_skips_disabled(monkeypatch):
    svc = _service()
    svc._scheduler = MagicMock()

    monkeypatch.setattr(
        scheduler.SchedulerService,
        "_TASK_REGISTRY",
        {
            "scan": {
                "func": "run_scan_job",
                "default_interval": "daily",
                "default_hour": 4,
                "description": "Library Scan",
            },
            "cleanup": {
                "func": "run_cleanup_job",
                "default_interval": "monthly",
                "default_hour": 3,
                "description": "Cleanup",
            },
        },
    )

    lookup = {
        "system.task.scan.interval": "daily",
        "system.task.cleanup.interval": "disabled",
    }
    svc._get_setting_value = MagicMock(side_effect=lambda key, default: lookup[key])
    svc._get_trigger_for_interval = MagicMock(side_effect=lambda interval, hour: f"{interval}-{hour}")

    svc.reschedule_jobs()

    svc._scheduler.remove_all_jobs.assert_called_once()
    svc._scheduler.add_job.assert_called_once()
    kwargs = svc._scheduler.add_job.call_args.kwargs
    assert kwargs["id"] == "scan"
    assert kwargs["trigger"] == "daily-4"
    assert kwargs["replace_existing"] is True


def test_get_trigger_for_interval_covers_all_mappings():
    daily = scheduler.SchedulerService._get_trigger_for_interval("daily", 1)
    weekly = scheduler.SchedulerService._get_trigger_for_interval("weekly", 2)
    monthly = scheduler.SchedulerService._get_trigger_for_interval("monthly", 3)
    fallback = scheduler.SchedulerService._get_trigger_for_interval("nonsense", 4)

    assert "hour='1'" in str(daily)
    assert "day_of_week='mon'" in str(weekly)
    assert "day='1'" in str(monthly)
    assert "day_of_week='mon'" in str(fallback)


def test_run_backup_job_success_and_failure(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(scheduler, "logger", logger)

    monkeypatch.setattr(
        scheduler.BackupService,
        "create_backup",
        MagicMock(return_value={"filename": "backup.zip"}),
    )
    scheduler.SchedulerService.run_backup_job()
    logger.info.assert_any_call("Backup Complete: backup.zip")

    monkeypatch.setattr(
        scheduler.BackupService,
        "create_backup",
        MagicMock(side_effect=RuntimeError("backup broke")),
    )
    scheduler.SchedulerService.run_backup_job()
    logger.error.assert_called_with("Backup Failed: backup broke")


def test_run_cleanup_job_queued_skipped_and_exception(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(scheduler, "logger", logger)

    add_cleanup = MagicMock(
        side_effect=[
            {"status": "queued", "job_id": 5},
            {"status": "ignored", "message": "already running"},
            RuntimeError("queue failed"),
        ]
    )
    monkeypatch.setattr(scheduler.scan_manager, "add_cleanup_task", add_cleanup)

    scheduler.SchedulerService.run_cleanup_job()
    scheduler.SchedulerService.run_cleanup_job()
    scheduler.SchedulerService.run_cleanup_job()

    logger.info.assert_any_call("Cleanup Job Queued: ID 5")
    logger.info.assert_any_call("Cleanup Skipped: already running")
    logger.error.assert_called_with("Failed to queue cleanup: queue failed")


def test_run_scan_job_handles_empty_success_and_error(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(scheduler, "logger", logger)
    add_task = MagicMock()
    monkeypatch.setattr(scheduler.scan_manager, "add_task", add_task)

    db_empty = MagicMock()
    q_empty = db_empty.query.return_value
    q_empty.all.return_value = []

    libraries = [SimpleNamespace(id=1, name="A"), SimpleNamespace(id=2, name="B")]
    db_with_libs = MagicMock()
    q_libs = db_with_libs.query.return_value
    q_libs.all.return_value = libraries

    db_error = MagicMock()
    db_error.query.side_effect = RuntimeError("query failed")

    session_local = MagicMock(side_effect=[db_empty, db_with_libs, db_error])
    monkeypatch.setattr(scheduler, "SessionLocal", session_local)

    scheduler.SchedulerService.run_scan_job()
    scheduler.SchedulerService.run_scan_job()
    scheduler.SchedulerService.run_scan_job()

    add_task.assert_any_call(1, force=False)
    add_task.assert_any_call(2, force=False)
    assert add_task.call_count == 2
    db_empty.close.assert_called_once()
    db_with_libs.close.assert_called_once()
    db_error.close.assert_called_once()
    logger.error.assert_called_with("Scheduled Scan Failed: query failed")
