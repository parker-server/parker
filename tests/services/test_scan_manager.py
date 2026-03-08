import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.models.job import JobStatus, JobType, ScanJob
from app.models.library import Library
import app.services.scan_manager as sm


def _manager():
    manager = object.__new__(sm.ScanManager)
    manager.logger = MagicMock()
    manager._stop_event = threading.Event()
    return manager


def _locked_error():
    return OperationalError("stmt", {}, Exception("database is locked"))


def _session_local_factory(db):
    return sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False)


def test_scan_manager_init_short_circuits_when_initialized():
    manager = object.__new__(sm.ScanManager)
    manager._initialized = True

    sm.ScanManager.__init__(manager)

    assert not hasattr(manager, "worker_thread")


def test_recover_interrupted_jobs_marks_running_as_failed(monkeypatch, db):
    manager = _manager()

    lib = Library(name="recover-lib", path="/tmp/recover-lib", is_scanning=True)
    running = ScanJob(library=lib, job_type=JobType.SCAN, status=JobStatus.RUNNING)
    pending = ScanJob(library=lib, job_type=JobType.SCAN, status=JobStatus.PENDING)
    db.add_all([lib, running, pending])
    db.commit()

    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))
    manager._recover_interrupted_jobs()

    db.refresh(running)
    db.refresh(pending)
    db.refresh(lib)

    assert running.status == JobStatus.FAILED
    assert running.error_message == "Scan interrupted by server restart"
    assert running.completed_at is not None
    assert pending.status == JobStatus.PENDING
    assert lib.is_scanning is False


def test_recover_interrupted_jobs_logs_exception(monkeypatch):
    manager = _manager()
    bad_db = MagicMock()
    bad_db.query.side_effect = RuntimeError("boom")

    monkeypatch.setattr(sm, "SessionLocal", lambda: bad_db)
    manager._recover_interrupted_jobs()

    manager.logger.error.assert_called()
    bad_db.close.assert_called_once()


def test_set_library_scanning_status_updates_and_handles_empty_id(monkeypatch, db):
    manager = _manager()

    lib = Library(name="set-flag-lib", path="/tmp/set-flag-lib", is_scanning=False)
    db.add(lib)
    db.commit()

    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))

    manager._set_library_scanning_status(0, True)
    db.refresh(lib)
    assert lib.is_scanning is False

    manager._set_library_scanning_status(lib.id, True)
    db.refresh(lib)
    assert lib.is_scanning is True


def test_set_library_scanning_status_retries_locked_db(monkeypatch):
    manager = _manager()

    locked_db = MagicMock()
    q1 = locked_db.query.return_value
    q1.filter.return_value = q1
    q1.update.side_effect = _locked_error()

    ok_db = MagicMock()
    q2 = ok_db.query.return_value
    q2.filter.return_value = q2
    q2.update.return_value = 1

    monkeypatch.setattr(sm, "SessionLocal", MagicMock(side_effect=[locked_db, ok_db]))
    sleep_mock = MagicMock()
    monkeypatch.setattr(sm.time, "sleep", sleep_mock)

    manager._set_library_scanning_status(42, True)

    sleep_mock.assert_called_once_with(0.5)
    assert ok_db.commit.call_count == 1


def test_set_library_scanning_status_logs_generic_error(monkeypatch):
    manager = _manager()

    bad_db = MagicMock()
    q = bad_db.query.return_value
    q.filter.return_value = q
    q.update.side_effect = RuntimeError("update broke")

    monkeypatch.setattr(sm, "SessionLocal", lambda: bad_db)

    manager._set_library_scanning_status(1, True)

    manager.logger.error.assert_called()


def test_safe_job_update_updates_summary_and_error(monkeypatch, db):
    manager = _manager()

    job = ScanJob(library_id=None, job_type=JobType.CLEANUP, status=JobStatus.PENDING)
    db.add(job)
    db.commit()

    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))

    summary = {"removed": 3}
    manager._safe_job_update(job.id, JobStatus.FAILED, summary=summary, error="broken")

    db.refresh(job)
    assert job.status == JobStatus.FAILED
    assert json.loads(job.result_summary) == summary
    assert job.error_message == "broken"
    assert job.completed_at is not None


def test_safe_job_update_retries_after_locked_db(monkeypatch):
    manager = _manager()

    job = SimpleNamespace(status=None, completed_at=None, result_summary=None, error_message=None)

    locked_db = MagicMock()
    locked_db.get.side_effect = _locked_error()

    ok_db = MagicMock()
    ok_db.get.return_value = job

    monkeypatch.setattr(sm, "SessionLocal", MagicMock(side_effect=[locked_db, ok_db]))
    sleep_mock = MagicMock()
    monkeypatch.setattr(sm.time, "sleep", sleep_mock)

    manager._safe_job_update(7, JobStatus.COMPLETED, summary={"ok": 1})

    sleep_mock.assert_called_once_with(1.0)
    assert ok_db.commit.call_count == 1
    assert job.status == JobStatus.COMPLETED


def test_safe_job_update_logs_critical_error(monkeypatch):
    manager = _manager()

    bad_db = MagicMock()
    bad_db.get.side_effect = RuntimeError("db blew up")

    monkeypatch.setattr(sm, "SessionLocal", lambda: bad_db)

    manager._safe_job_update(9, JobStatus.FAILED, error="x")

    manager.logger.error.assert_called()


def test_add_task_returns_queued_then_ignored(monkeypatch, db):
    manager = _manager()
    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))

    first = manager.add_task(5, force=True)
    assert first["status"] == "queued"

    second = manager.add_task(5, force=False)
    assert second["status"] == "ignored"
    assert second["message"] == "Scan active"


def test_add_cleanup_task_and_add_thumbnail_task(monkeypatch, db):
    manager = _manager()
    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))

    cleanup_queued = manager.add_cleanup_task()
    assert cleanup_queued["status"] == "queued"

    cleanup_ignored = manager.add_cleanup_task()
    assert cleanup_ignored["status"] == "ignored"

    thumb_queued = manager.add_thumbnail_task(10, force=True)
    assert thumb_queued["status"] == "queued"

    thumb_ignored = manager.add_thumbnail_task(10, force=False)
    assert thumb_ignored["status"] == "ignored"


def test_fix_stuck_libraries_resets_only_without_running_job(monkeypatch, db):
    manager = _manager()

    lib_active = Library(name="lib-active", path="/tmp/lib-active", is_scanning=True)
    lib_stuck = Library(name="lib-stuck", path="/tmp/lib-stuck", is_scanning=True)
    db.add_all([lib_active, lib_stuck])
    db.flush()

    db.add(ScanJob(library_id=lib_active.id, job_type=JobType.SCAN, status=JobStatus.RUNNING))
    db.commit()

    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))
    manager._fix_stuck_libraries()

    db.refresh(lib_active)
    db.refresh(lib_stuck)

    assert lib_active.is_scanning is True
    assert lib_stuck.is_scanning is False


def test_fix_stuck_libraries_swallows_exceptions(monkeypatch):
    manager = _manager()

    bad_db = MagicMock()
    bad_db.query.side_effect = RuntimeError("bad query")

    monkeypatch.setattr(sm, "SessionLocal", lambda: bad_db)

    manager._fix_stuck_libraries()

    bad_db.close.assert_called_once()


def test_run_scan_job_success_updates_and_queues_followup(monkeypatch, db):
    manager = _manager()
    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))

    lib = Library(name="scan-lib", path="/tmp/scan-lib")
    db.add(lib)
    db.commit()

    scanner_mock = MagicMock()
    scanner_mock.scan.return_value = {"imported": 2, "updated": 1, "deleted": 0, "errors": 0, "elapsed": 1.1}
    monkeypatch.setattr(sm, "LibraryScanner", lambda library, session: scanner_mock)

    manager._safe_job_update = MagicMock()

    manager._run_scan_job({"id": 99, "library_id": lib.id, "force": True})

    manager._safe_job_update.assert_called_once()
    args = manager._safe_job_update.call_args
    assert args.args[0] == 99
    assert args.args[1] == JobStatus.COMPLETED
    assert args.kwargs["summary"]["imported"] == 2

    db.expire_all()
    queued = db.query(ScanJob).all()
    assert len(queued) == 2
    assert {row.job_type for row in queued} == {JobType.THUMBNAIL, JobType.CLEANUP}


def test_run_scan_job_handles_missing_library(monkeypatch, db):
    manager = _manager()
    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))

    manager._safe_job_update = MagicMock()
    manager._set_library_scanning_status = MagicMock()

    manager._run_scan_job({"id": 7, "library_id": 12345, "force": False})

    manager._safe_job_update.assert_called_once_with(7, JobStatus.FAILED, error="Library not found")
    manager._set_library_scanning_status.assert_called_once_with(12345, False)


def test_run_scan_job_handles_scanner_exception(monkeypatch, db):
    manager = _manager()
    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))

    lib = Library(name="scan-fail-lib", path="/tmp/scan-fail-lib")
    db.add(lib)
    db.commit()

    scanner_mock = MagicMock()
    scanner_mock.scan.side_effect = RuntimeError("scan exploded")
    monkeypatch.setattr(sm, "LibraryScanner", lambda library, session: scanner_mock)
    monkeypatch.setattr(sm.traceback, "print_exc", MagicMock())

    manager._safe_job_update = MagicMock()
    manager._set_library_scanning_status = MagicMock()

    manager._run_scan_job({"id": 8, "library_id": lib.id, "force": False})

    call = manager._safe_job_update.call_args
    assert call.args[0] == 8
    assert call.args[1] == JobStatus.FAILED
    assert "scan exploded" in call.kwargs["error"]
    manager._set_library_scanning_status.assert_called_once_with(lib.id, False)


def test_run_scan_job_logs_followup_queue_failure(monkeypatch):
    manager = _manager()

    scan_db = MagicMock()
    scan_db.get.return_value = SimpleNamespace(id=1)

    queue_db = MagicMock()
    queue_db.commit.side_effect = RuntimeError("queue boom")

    monkeypatch.setattr(sm, "SessionLocal", MagicMock(side_effect=[scan_db, queue_db]))

    scanner_mock = MagicMock()
    scanner_mock.scan.return_value = {"imported": 1}
    monkeypatch.setattr(sm, "LibraryScanner", lambda library, session: scanner_mock)

    manager._safe_job_update = MagicMock()

    manager._run_scan_job({"id": 100, "library_id": 1, "force": False})

    manager.logger.error.assert_called()


def test_run_thumbnail_job_success_parallel_toggle(monkeypatch, db):
    manager = _manager()
    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))

    service = MagicMock()
    service.process_missing_thumbnails_parallel.return_value = {"processed": 5}
    monkeypatch.setattr(sm, "ThumbnailService", lambda session, library_id: service)
    monkeypatch.setattr(sm, "get_cached_setting", lambda key, default: True)

    manager._safe_job_update = MagicMock()
    manager._set_library_scanning_status = MagicMock()

    manager._run_thumbnail_job({"id": 11, "library_id": 55, "force": True})

    service.process_missing_thumbnails_parallel.assert_called_once_with(force=True, worker_limit=0)
    manager._safe_job_update.assert_called_once_with(11, JobStatus.COMPLETED, summary={"processed": 5})
    manager._set_library_scanning_status.assert_called_once_with(55, False)


def test_run_thumbnail_job_failure_marks_job_failed(monkeypatch, db):
    manager = _manager()
    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))

    service = MagicMock()
    service.process_missing_thumbnails_parallel.side_effect = RuntimeError("thumb fail")
    monkeypatch.setattr(sm, "ThumbnailService", lambda session, library_id: service)
    monkeypatch.setattr(sm, "get_cached_setting", lambda key, default: False)
    monkeypatch.setattr(sm.traceback, "print_exc", MagicMock())

    manager._safe_job_update = MagicMock()
    manager._set_library_scanning_status = MagicMock()

    manager._run_thumbnail_job({"id": 12, "library_id": 56, "force": False})

    manager._safe_job_update.assert_called_once()
    call = manager._safe_job_update.call_args
    assert call.args[0] == 12
    assert call.args[1] == JobStatus.FAILED
    assert "thumb fail" in call.kwargs["error"]
    manager._set_library_scanning_status.assert_called_once_with(56, False)


def test_run_cleanup_job_global_and_scoped_paths(monkeypatch, db):
    manager = _manager()
    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))

    maintenance_global = MagicMock()
    maintenance_global.cleanup_missing_files.return_value = [1, 2]
    maintenance_global.cleanup_orphans.return_value = {"series_removed": 1}
    maintenance_global.cleanup_orphaned_thumbnails.return_value = {"deleted": 9}

    maintenance_scoped = MagicMock()
    maintenance_scoped.cleanup_missing_files.return_value = []
    maintenance_scoped.cleanup_orphans.return_value = {"series_removed": 0}

    monkeypatch.setattr(sm, "MaintenanceService", MagicMock(side_effect=[maintenance_global, maintenance_scoped]))

    manager._safe_job_update = MagicMock()
    manager._set_library_scanning_status = MagicMock()

    manager._run_cleanup_job({"id": 20, "library_id": None})
    manager._run_cleanup_job({"id": 21, "library_id": 77})

    first = manager._safe_job_update.call_args_list[0]
    second = manager._safe_job_update.call_args_list[1]

    assert first.args[0] == 20 and first.args[1] == JobStatus.COMPLETED
    assert first.kwargs["summary"]["missing_files_removed"] == 2
    assert first.kwargs["summary"]["orphaned_thumbnails_deleted"] == {"deleted": 9}

    assert second.args[0] == 21 and second.args[1] == JobStatus.COMPLETED
    assert second.kwargs["summary"]["missing_files_removed"] == 0

    maintenance_global.delete_thumbnails_by_id.assert_called_once_with([1, 2])
    maintenance_scoped.cleanup_orphaned_thumbnails.assert_not_called()
    manager._set_library_scanning_status.assert_called_once_with(77, False)


def test_run_cleanup_job_failure_marks_failed(monkeypatch, db):
    manager = _manager()
    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))

    maintenance = MagicMock()
    maintenance.cleanup_missing_files.side_effect = RuntimeError("cleanup fail")
    monkeypatch.setattr(sm, "MaintenanceService", lambda session: maintenance)

    manager._safe_job_update = MagicMock()
    manager._set_library_scanning_status = MagicMock()

    manager._run_cleanup_job({"id": 22, "library_id": 88})

    call = manager._safe_job_update.call_args
    assert call.args[0] == 22
    assert call.args[1] == JobStatus.FAILED
    assert "cleanup fail" in call.kwargs["error"]
    manager._set_library_scanning_status.assert_called_once_with(88, False)


def test_process_queue_dispatches_scan_job(monkeypatch):
    manager = _manager()

    job = SimpleNamespace(id=5, library_id=9, job_type=JobType.SCAN, force_scan=True)

    pick = MagicMock()
    pick.filter.return_value = pick
    pick.order_by.return_value = pick
    pick.first.return_value = job

    claim = MagicMock()
    claim.filter.return_value = claim
    claim.update.return_value = 1

    db = MagicMock()
    db.query.side_effect = [pick, claim]

    manager._stop_event = MagicMock()
    manager._stop_event.is_set.side_effect = [False, True]

    monkeypatch.setattr(sm, "SessionLocal", lambda: db)
    monkeypatch.setattr(sm.time, "sleep", MagicMock())

    manager._set_library_scanning_status = MagicMock()
    manager._run_scan_job = MagicMock()
    manager._run_thumbnail_job = MagicMock()
    manager._run_cleanup_job = MagicMock()

    manager._process_queue()

    manager._set_library_scanning_status.assert_called_once_with(9, True)
    manager._run_scan_job.assert_called_once_with({"id": 5, "library_id": 9, "type": JobType.SCAN, "force": True})
    manager._run_thumbnail_job.assert_not_called()
    manager._run_cleanup_job.assert_not_called()


@pytest.mark.parametrize(
    "job_type,runner_name",
    [
        (JobType.THUMBNAIL, "_run_thumbnail_job"),
        (JobType.CLEANUP, "_run_cleanup_job"),
    ],
)
def test_process_queue_dispatches_non_scan_jobs(monkeypatch, job_type, runner_name):
    manager = _manager()

    job = SimpleNamespace(id=6, library_id=None, job_type=job_type, force_scan=False)

    scan_q = MagicMock()
    scan_q.filter.return_value = scan_q
    scan_q.order_by.return_value = scan_q
    scan_q.first.return_value = None

    thumb_or_cleanup_q = MagicMock()
    thumb_or_cleanup_q.filter.return_value = thumb_or_cleanup_q
    thumb_or_cleanup_q.order_by.return_value = thumb_or_cleanup_q
    thumb_or_cleanup_q.first.return_value = job

    claim = MagicMock()
    claim.filter.return_value = claim
    claim.update.return_value = 1

    db = MagicMock()
    if job_type == JobType.THUMBNAIL:
        db.query.side_effect = [scan_q, thumb_or_cleanup_q, claim]
    else:
        second_none = MagicMock()
        second_none.filter.return_value = second_none
        second_none.order_by.return_value = second_none
        second_none.first.return_value = None
        db.query.side_effect = [scan_q, second_none, thumb_or_cleanup_q, claim]

    manager._stop_event = MagicMock()
    manager._stop_event.is_set.side_effect = [False, True]

    monkeypatch.setattr(sm, "SessionLocal", lambda: db)
    monkeypatch.setattr(sm.time, "sleep", MagicMock())

    manager._set_library_scanning_status = MagicMock()
    manager._run_scan_job = MagicMock()
    manager._run_thumbnail_job = MagicMock()
    manager._run_cleanup_job = MagicMock()

    manager._process_queue()

    getattr(manager, runner_name).assert_called_once()


def test_process_queue_no_job_triggers_integrity_check(monkeypatch):
    manager = _manager()

    class NoJobDB:
        def query(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def first(self):
            return None

        def close(self):
            return None

    db = NoJobDB()

    manager._stop_event = MagicMock()
    manager._stop_event.is_set.side_effect = [False] * 15 + [True]

    monkeypatch.setattr(sm, "SessionLocal", lambda: db)
    sleep_mock = MagicMock()
    monkeypatch.setattr(sm.time, "sleep", sleep_mock)

    manager._fix_stuck_libraries = MagicMock()

    manager._process_queue()

    manager._fix_stuck_libraries.assert_called_once()
    assert sleep_mock.call_count == 15


def test_process_queue_handles_worker_exception(monkeypatch):
    manager = _manager()

    db = MagicMock()
    db.query.side_effect = RuntimeError("poll boom")

    manager._stop_event = MagicMock()
    manager._stop_event.is_set.side_effect = [False, True]

    monkeypatch.setattr(sm, "SessionLocal", lambda: db)
    sleep_mock = MagicMock()
    monkeypatch.setattr(sm.time, "sleep", sleep_mock)

    manager._process_queue()

    manager.logger.error.assert_called()
    sleep_mock.assert_called_with(5)


def test_process_queue_skips_when_atomic_claim_lost(monkeypatch):
    manager = _manager()

    job = SimpleNamespace(id=3, library_id=10, job_type=JobType.THUMBNAIL, force_scan=False)

    pick = MagicMock()
    pick.filter.return_value = pick
    pick.order_by.return_value = pick
    pick.first.return_value = job

    claim = MagicMock()
    claim.filter.return_value = claim
    claim.update.return_value = 0

    db = MagicMock()
    db.query.side_effect = [pick, claim]

    manager._stop_event = MagicMock()
    manager._stop_event.is_set.side_effect = [False, True]

    monkeypatch.setattr(sm, "SessionLocal", lambda: db)
    monkeypatch.setattr(sm.time, "sleep", MagicMock())

    manager._run_thumbnail_job = MagicMock()

    manager._process_queue()

    manager._run_thumbnail_job.assert_not_called()


def test_set_library_scanning_status_logs_non_locked_operational_error(monkeypatch):
    manager = _manager()

    db_mock = MagicMock()
    q = db_mock.query.return_value
    q.filter.return_value = q
    q.update.side_effect = [OperationalError("stmt", {}, Exception("other failure")), 1]

    monkeypatch.setattr(sm, "SessionLocal", lambda: db_mock)

    manager._set_library_scanning_status(7, True)

    manager.logger.error.assert_called()


def test_safe_job_update_logs_non_locked_operational_error(monkeypatch):
    manager = _manager()

    job = SimpleNamespace(status=None, completed_at=None, result_summary=None, error_message=None)

    db_mock = MagicMock()
    db_mock.get.side_effect = [OperationalError("stmt", {}, Exception("other failure")), job]

    monkeypatch.setattr(sm, "SessionLocal", lambda: db_mock)

    manager._safe_job_update(77, JobStatus.COMPLETED)

    manager.logger.error.assert_called()
