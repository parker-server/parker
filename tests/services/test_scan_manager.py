import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, ANY

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from app.models.job import ENRICHMENT_LANE, JobStatus, JobType, ScanJob
import app.services.scan_manager as sm
from tests.factories import create_library_with_root


def _manager():
    manager = object.__new__(sm.ScanManager)
    manager.logger = MagicMock()
    manager._stop_event = threading.Event()
    manager.worker_threads = {}
    manager._last_external_review_at = None
    return manager


class _FakeThread:
    instances = []

    def __init__(self, target=None, args=(), name=None, daemon=False):
        self.target = target
        self.args = args
        self.name = name
        self.daemon = daemon
        self.started = False
        self.join_timeout = None
        self.alive = False
        _FakeThread.instances.append(self)

    def start(self):
        self.started = True
        self.alive = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.join_timeout = timeout
        self.alive = False


def _locked_error():
    return OperationalError("stmt", {}, Exception("database is locked"))


def _session_local_factory(db):
    return sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False)


def test_scan_manager_init_short_circuits_when_initialized():
    manager = object.__new__(sm.ScanManager)
    manager._initialized = True

    sm.ScanManager.__init__(manager)

    assert not hasattr(manager, "worker_threads")


def test_scan_manager_init_does_not_start_worker(monkeypatch):
    manager = object.__new__(sm.ScanManager)
    manager._initialized = False
    thread_factory = MagicMock()
    monkeypatch.setattr(sm.threading, "Thread", thread_factory)

    sm.ScanManager.__init__(manager)

    assert manager.worker_threads == {}
    thread_factory.assert_not_called()


def test_scan_manager_start_recovers_and_starts_worker(monkeypatch):
    manager = _manager()
    manager._recover_interrupted_jobs = MagicMock()

    _FakeThread.instances.clear()
    monkeypatch.setattr(sm.threading, "Thread", _FakeThread)

    manager.start()

    manager._recover_interrupted_jobs.assert_called_once()
    library_thread, enrichment_thread = _FakeThread.instances
    assert library_thread.target == manager._process_lane
    assert library_thread.args == (sm.LIBRARY_LANE,)
    assert enrichment_thread.args == (ENRICHMENT_LANE,)
    assert all(thread.daemon and thread.started for thread in _FakeThread.instances)
    assert manager.worker_threads == {sm.LIBRARY_LANE: library_thread, ENRICHMENT_LANE: enrichment_thread}


def test_scan_manager_start_launches_one_worker_per_lane(monkeypatch):
    manager = _manager()
    manager._recover_interrupted_jobs = MagicMock()

    monkeypatch.setattr(sm, "JOB_LANES", {
        sm.LIBRARY_LANE: (JobType.SCAN,),
        "other": (JobType.CLEANUP,),
    })
    _FakeThread.instances.clear()
    monkeypatch.setattr(sm.threading, "Thread", _FakeThread)

    manager.start()

    # Recovery runs once for the whole manager, not once per lane
    manager._recover_interrupted_jobs.assert_called_once()
    assert [thread.args for thread in _FakeThread.instances] == [(sm.LIBRARY_LANE,), ("other",)]
    assert all(thread.started and thread.daemon for thread in _FakeThread.instances)
    assert set(manager.worker_threads) == {sm.LIBRARY_LANE, "other"}


def test_scan_manager_start_ignores_already_running_worker(monkeypatch):
    manager = _manager()
    running_thread = _FakeThread()
    running_thread.alive = True
    manager.worker_threads = {sm.LIBRARY_LANE: running_thread}
    manager._recover_interrupted_jobs = MagicMock()

    thread_factory = MagicMock()
    monkeypatch.setattr(sm.threading, "Thread", thread_factory)

    manager.start()

    manager._recover_interrupted_jobs.assert_not_called()
    thread_factory.assert_not_called()


def test_scan_manager_stop_joins_worker():
    manager = _manager()
    thread = _FakeThread()
    thread.alive = True
    manager.worker_threads = {sm.LIBRARY_LANE: thread}

    manager.stop(timeout=2.5)

    assert manager._stop_event.is_set()
    assert thread.join_timeout == 2.5
    assert manager.worker_threads == {}


def test_scan_manager_stop_keeps_worker_that_does_not_exit():
    manager = _manager()

    stopped = _FakeThread()
    stopped.alive = True

    stuck = _FakeThread()
    stuck.alive = True
    stuck.join = MagicMock()  # join returns but the thread stays alive

    manager.worker_threads = {sm.LIBRARY_LANE: stopped, "other": stuck}

    manager.stop(timeout=1.0)

    stuck.join.assert_called_once_with(timeout=1.0)
    assert manager.worker_threads == {"other": stuck}
    manager.logger.warning.assert_called_once()


def test_recover_interrupted_jobs_marks_running_as_failed(monkeypatch, db):
    manager = _manager()

    lib = create_library_with_root(db, "recover-lib", "/tmp/recover-lib", is_scanning=True)
    running = ScanJob(library=lib, job_type=JobType.SCAN, status=JobStatus.RUNNING)
    pending = ScanJob(library=lib, job_type=JobType.SCAN, status=JobStatus.PENDING)
    db.add_all([running, pending])
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

    lib = create_library_with_root(db, "set-flag-lib", "/tmp/set-flag-lib", is_scanning=False)
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

    rehydrate_queued = manager.add_metadata_rehydrate_task(10)
    assert rehydrate_queued["status"] == "queued"

    rehydrate_ignored = manager.add_metadata_rehydrate_task(10)
    assert rehydrate_ignored["status"] == "ignored"


def test_fix_stuck_libraries_resets_only_without_running_job(monkeypatch, db):
    manager = _manager()

    lib_active = create_library_with_root(db, "lib-active", "/tmp/lib-active", is_scanning=True)
    lib_stuck = create_library_with_root(db, "lib-stuck", "/tmp/lib-stuck", is_scanning=True)
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

    lib = create_library_with_root(db, "scan-lib", "/tmp/scan-lib")
    db.commit()

    error_details = [{"file_path": "/tmp/jobs-lib/bad.cbz", "message": "bad archive"}]
    scanner_mock = MagicMock()
    scanner_mock.scan_parallel.return_value = {
        "imported": 2,
        "updated": 1,
        "deleted": 0,
        "skipped": 3,
        "errors": 1,
        "error_details": error_details,
        "elapsed": 1.1,
    }
    monkeypatch.setattr(sm, "LibraryScanner", lambda library, session: scanner_mock)
    monkeypatch.setattr(sm, "get_cached_setting", lambda key, default: True)

    manager._safe_job_update = MagicMock()

    manager._run_scan_job({"id": 99, "library_id": lib.id, "force": True})

    scanner_mock.scan_parallel.assert_called_once_with(force=True, worker_limit=0)
    manager._safe_job_update.assert_called_once()
    args = manager._safe_job_update.call_args
    assert args.args[0] == 99
    assert args.args[1] == JobStatus.COMPLETED
    assert args.kwargs["summary"]["imported"] == 2
    assert args.kwargs["summary"]["skipped"] == 3
    assert args.kwargs["summary"]["errors"] == 1
    assert args.kwargs["summary"]["error_details"] == error_details

    db.expire_all()
    queued = db.query(ScanJob).all()
    assert len(queued) == 2
    assert {row.job_type for row in queued} == {JobType.THUMBNAIL, JobType.CLEANUP}


def test_run_scan_job_writer_failure_marks_failed_but_keeps_counts_and_queues_followup(monkeypatch, db):
    manager = _manager()
    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))

    lib = create_library_with_root(db, "scan-writer-fail-lib", "/tmp/scan-writer-fail-lib")
    db.commit()

    error_details = [{"file_path": None, "message": "writer boom"}]
    scanner_mock = MagicMock()
    scanner_mock.scan_parallel.return_value = {
        "imported": 800,
        "updated": 0,
        "deleted": 0,
        "skipped": 0,
        "errors": 1,
        "error_details": error_details,
        "fatal_error": "writer boom",
        "elapsed": 64.5,
    }
    monkeypatch.setattr(sm, "LibraryScanner", lambda library, session: scanner_mock)
    monkeypatch.setattr(sm, "get_cached_setting", lambda key, default: False)
    update_last_scanned = MagicMock()
    monkeypatch.setattr(sm.ScanManager, "update_library_last_scanned", update_last_scanned)

    manager._safe_job_update = MagicMock()

    manager._run_scan_job({"id": 98, "library_id": lib.id, "force": False})

    manager._safe_job_update.assert_called_once()
    args = manager._safe_job_update.call_args
    assert args.args[0] == 98
    assert args.args[1] == JobStatus.FAILED
    assert args.kwargs["error"] == "writer boom"
    assert args.kwargs["summary"]["imported"] == 800
    assert args.kwargs["summary"]["error_details"] == error_details

    # The library is not reported as freshly scanned...
    update_last_scanned.assert_not_called()

    # ...but comics committed before the failure still get thumbnails and cleanup.
    db.expire_all()
    queued = db.query(ScanJob).all()
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

    lib = create_library_with_root(db, "scan-fail-lib", "/tmp/scan-fail-lib")
    db.commit()

    scanner_mock = MagicMock()
    scanner_mock.scan_parallel.side_effect = RuntimeError("scan exploded")
    monkeypatch.setattr(sm, "LibraryScanner", lambda library, session: scanner_mock)
    monkeypatch.setattr(sm, "get_cached_setting", lambda key, default: False)

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
    monkeypatch.setattr(sm.ScanManager, "update_library_last_scanned", MagicMock())

    scanner_mock = MagicMock()
    scanner_mock.scan_parallel.return_value = {"imported": 1, "updated": 0, "deleted": 0, "errors": 0, "elapsed": 1.0}
    monkeypatch.setattr(sm, "LibraryScanner", lambda library, session: scanner_mock)
    monkeypatch.setattr(sm, "get_cached_setting", lambda key, default: False)

    manager._safe_job_update = MagicMock()
    manager._refresh_database_statistics = MagicMock()

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
    maintenance_global.cleanup_orphaned_cover_overrides.return_value = 3
    maintenance_global.cleanup_old_jobs.return_value = 4

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
    assert first.kwargs["summary"]["orphaned_cover_overrides_deleted"] == 3
    assert first.kwargs["summary"]["old_jobs"] == 4

    assert second.args[0] == 21 and second.args[1] == JobStatus.COMPLETED
    assert second.kwargs["summary"]["missing_files_removed"] == 0
    assert "orphaned_cover_overrides_deleted" not in second.kwargs["summary"]

    maintenance_global.delete_thumbnails_by_id.assert_called_once_with([1, 2])
    maintenance_global.cleanup_old_jobs.assert_called_once()
    maintenance_scoped.cleanup_old_jobs.assert_not_called()
    maintenance_scoped.cleanup_orphaned_thumbnails.assert_not_called()
    maintenance_scoped.cleanup_orphaned_cover_overrides.assert_not_called()
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


def test_run_metadata_rehydrate_job_success(monkeypatch, db):
    manager = _manager()
    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))

    lib = create_library_with_root(
        db,
        "rehydrate-lib",
        "/tmp/rehydrate-lib",
        parse_reading_lists=True,
        parse_collections=False,
        parse_story_arcs=True,
    )
    db.commit()

    expected_summary = {"comics_scanned": 12, "reading_lists_restored": 3}
    rehydrate = MagicMock(return_value=expected_summary)
    monkeypatch.setattr(sm, "rehydrate_library_metadata_from_cache", rehydrate)

    manager._safe_job_update = MagicMock()
    manager._set_library_scanning_status = MagicMock()

    manager._run_metadata_rehydrate_job({"id": 31, "library_id": lib.id})

    rehydrate.assert_called_once_with(
        db=ANY,
        library_id=lib.id,
        rehydrate_reading_lists=True,
        rehydrate_collections=False,
        rehydrate_story_arcs=True,
    )
    manager._safe_job_update.assert_called_once_with(31, JobStatus.COMPLETED, summary=expected_summary)
    manager._set_library_scanning_status.assert_called_once_with(lib.id, False)


def test_run_metadata_rehydrate_job_missing_library(monkeypatch, db):
    manager = _manager()
    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))

    manager._safe_job_update = MagicMock()
    manager._set_library_scanning_status = MagicMock()

    manager._run_metadata_rehydrate_job({"id": 32, "library_id": 999999})

    manager._safe_job_update.assert_called_once_with(32, JobStatus.FAILED, error="Library not found")
    manager._set_library_scanning_status.assert_called_once_with(999999, False)


def test_run_metadata_rehydrate_job_failure_marks_failed(monkeypatch, db):
    manager = _manager()
    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))

    lib = create_library_with_root(db, "rehydrate-fail", "/tmp/rehydrate-fail")
    db.commit()

    monkeypatch.setattr(
        sm,
        "rehydrate_library_metadata_from_cache",
        MagicMock(side_effect=RuntimeError("rehydrate fail")),
    )

    manager._safe_job_update = MagicMock()
    manager._set_library_scanning_status = MagicMock()

    manager._run_metadata_rehydrate_job({"id": 33, "library_id": lib.id})

    call = manager._safe_job_update.call_args
    assert call.args[0] == 33
    assert call.args[1] == JobStatus.FAILED
    assert "rehydrate fail" in call.kwargs["error"]
    manager._set_library_scanning_status.assert_called_once_with(lib.id, False)


def test_process_lane_dispatches_scan_job(monkeypatch):
    manager = _manager()

    job = SimpleNamespace(id=5, library_id=9, job_type=JobType.SCAN, force_scan=True, target_id=None, payload_json=None)

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

    manager._process_lane(sm.LIBRARY_LANE)

    manager._set_library_scanning_status.assert_called_once_with(9, True)
    manager._run_scan_job.assert_called_once_with({"id": 5, "library_id": 9, "type": JobType.SCAN, "force": True, "target_id": None, "payload": None})
    manager._run_thumbnail_job.assert_not_called()
    manager._run_cleanup_job.assert_not_called()


@pytest.mark.parametrize(
    "job_type,runner_name",
    [
        (JobType.THUMBNAIL, "_run_thumbnail_job"),
        (JobType.CLEANUP, "_run_cleanup_job"),
    ],
)
def test_process_lane_dispatches_non_scan_jobs(monkeypatch, job_type, runner_name):
    manager = _manager()

    job = SimpleNamespace(id=6, library_id=None, job_type=job_type, force_scan=False, target_id=None, payload_json=None)

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
    manager._run_metadata_rehydrate_job = MagicMock()

    manager._process_lane(sm.LIBRARY_LANE)

    getattr(manager, runner_name).assert_called_once()
    manager._run_metadata_rehydrate_job.assert_not_called()


def test_process_lane_dispatches_metadata_rehydrate_job(monkeypatch):
    manager = _manager()

    job = SimpleNamespace(id=7, library_id=12, job_type=JobType.METADATA_REHYDRATE, force_scan=False, target_id=None, payload_json=None)

    scan_q = MagicMock()
    scan_q.filter.return_value = scan_q
    scan_q.order_by.return_value = scan_q
    scan_q.first.return_value = None

    thumb_q = MagicMock()
    thumb_q.filter.return_value = thumb_q
    thumb_q.order_by.return_value = thumb_q
    thumb_q.first.return_value = None

    cleanup_q = MagicMock()
    cleanup_q.filter.return_value = cleanup_q
    cleanup_q.order_by.return_value = cleanup_q
    cleanup_q.first.return_value = None

    metadata_q = MagicMock()
    metadata_q.filter.return_value = metadata_q
    metadata_q.order_by.return_value = metadata_q
    metadata_q.first.return_value = job

    claim = MagicMock()
    claim.filter.return_value = claim
    claim.update.return_value = 1

    db = MagicMock()
    db.query.side_effect = [scan_q, thumb_q, cleanup_q, metadata_q, claim]

    manager._stop_event = MagicMock()
    manager._stop_event.is_set.side_effect = [False, True]

    monkeypatch.setattr(sm, "SessionLocal", lambda: db)
    monkeypatch.setattr(sm.time, "sleep", MagicMock())

    manager._set_library_scanning_status = MagicMock()
    manager._run_scan_job = MagicMock()
    manager._run_thumbnail_job = MagicMock()
    manager._run_cleanup_job = MagicMock()
    manager._run_metadata_rehydrate_job = MagicMock()

    manager._process_lane(sm.LIBRARY_LANE)

    manager._set_library_scanning_status.assert_called_once_with(12, True)
    manager._run_metadata_rehydrate_job.assert_called_once_with(
        {"id": 7, "library_id": 12, "type": JobType.METADATA_REHYDRATE, "force": False, "target_id": None, "payload": None}
    )


def test_process_lane_no_job_triggers_integrity_check(monkeypatch):
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

    manager._process_lane(sm.LIBRARY_LANE)

    manager._fix_stuck_libraries.assert_called_once()
    assert sleep_mock.call_count == 15


def test_process_lane_handles_worker_exception(monkeypatch):
    manager = _manager()

    db = MagicMock()
    db.query.side_effect = RuntimeError("poll boom")

    manager._stop_event = MagicMock()
    manager._stop_event.is_set.side_effect = [False, True]

    monkeypatch.setattr(sm, "SessionLocal", lambda: db)
    sleep_mock = MagicMock()
    monkeypatch.setattr(sm.time, "sleep", sleep_mock)

    manager._process_lane(sm.LIBRARY_LANE)

    manager.logger.error.assert_called()
    sleep_mock.assert_called_with(5)


def test_process_lane_skips_when_atomic_claim_lost(monkeypatch):
    manager = _manager()

    job = SimpleNamespace(id=3, library_id=10, job_type=JobType.THUMBNAIL, force_scan=False, target_id=None, payload_json=None)

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

    manager._process_lane(sm.LIBRARY_LANE)

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


def test_library_lane_keeps_original_job_type_priority():
    assert sm.JOB_LANES[sm.LIBRARY_LANE] == (
        JobType.SCAN,
        JobType.THUMBNAIL,
        JobType.CLEANUP,
        JobType.METADATA_REHYDRATE,
    )


def test_process_lane_only_claims_its_own_job_types(monkeypatch, db):
    manager = _manager()

    scan_job = ScanJob(library_id=None, job_type=JobType.SCAN, status=JobStatus.PENDING)
    cleanup_job = ScanJob(library_id=None, job_type=JobType.CLEANUP, status=JobStatus.PENDING)
    db.add_all([scan_job, cleanup_job])
    db.commit()

    monkeypatch.setattr(sm, "JOB_LANES", {
        sm.LIBRARY_LANE: (JobType.SCAN,),
        "other": (JobType.CLEANUP,),
    })
    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))
    monkeypatch.setattr(sm.time, "sleep", MagicMock())

    manager._stop_event = MagicMock()
    manager._stop_event.is_set.side_effect = [False, True]
    manager._run_scan_job = MagicMock()
    manager._run_cleanup_job = MagicMock()

    # The higher-priority SCAN job belongs to another lane, so it must be left alone
    manager._process_lane("other")

    manager._run_scan_job.assert_not_called()
    manager._run_cleanup_job.assert_called_once()
    assert manager._run_cleanup_job.call_args.args[0]["id"] == cleanup_job.id

    db.expire_all()
    assert db.get(ScanJob, scan_job.id).status == JobStatus.PENDING
    assert db.get(ScanJob, cleanup_job.id).status == JobStatus.RUNNING


def test_process_lane_skips_integrity_check_outside_library_lane(monkeypatch):
    manager = _manager()

    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value = query
    query.order_by.return_value = query
    query.first.return_value = None

    monkeypatch.setattr(sm, "JOB_LANES", {"other": (JobType.CLEANUP,)})
    monkeypatch.setattr(sm, "SessionLocal", lambda: db)
    monkeypatch.setattr(sm.time, "sleep", MagicMock())

    manager._stop_event = MagicMock()
    manager._stop_event.is_set.side_effect = [False] * 15 + [True]
    manager._fix_stuck_libraries = MagicMock()

    manager._process_lane("other")

    manager._fix_stuck_libraries.assert_not_called()


def test_active_external_review_job_index_rejects_duplicates(db):
    def lookup_job(comic_id, status=JobStatus.PENDING):
        return ScanJob(
            job_type="external_review_lookup",
            target_type="comic",
            target_id=comic_id,
            payload_json=json.dumps({"provider": "comicbookroundup"}),
            status=status,
        )

    # Finished jobs and other targets don't count as duplicates
    db.add_all([
        lookup_job(1, status=JobStatus.COMPLETED),
        lookup_job(1, status=JobStatus.FAILED),
        lookup_job(1),
        lookup_job(2, status=JobStatus.RUNNING),
    ])
    db.commit()

    db.add(lookup_job(1, status=JobStatus.RUNNING))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    # Library jobs are not constrained by the index
    db.add_all([
        ScanJob(library_id=None, job_type=JobType.CLEANUP, status=JobStatus.PENDING),
        ScanJob(library_id=None, job_type=JobType.CLEANUP, status=JobStatus.PENDING),
    ])
    db.commit()


# --- External review lookup jobs (enrichment lane) ---

def _review_job_data(job_id=40, comic_id=123):
    return {
        "id": job_id,
        "library_id": None,
        "type": JobType.EXTERNAL_REVIEW_LOOKUP,
        "force": False,
        "target_id": comic_id,
        "payload": json.dumps({"provider": "comicbookroundup"}),
    }


def test_review_lookups_run_in_their_own_lane():
    assert sm.JOB_LANES[ENRICHMENT_LANE] == (JobType.EXTERNAL_REVIEW_LOOKUP,)
    assert JobType.EXTERNAL_REVIEW_LOOKUP not in sm.JOB_LANES[sm.LIBRARY_LANE]


def test_add_external_review_task_queues_one_active_job_per_comic(monkeypatch, db):
    manager = _manager()
    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))

    first = manager.add_external_review_task(123)
    assert first["status"] == "queued"

    # A repeat request while the first is active gets the existing job back
    repeat = manager.add_external_review_task(123)
    assert repeat == {
        "status": "ignored",
        "job_id": first["job_id"],
        "message": "External review lookup already active",
    }

    other_comic = manager.add_external_review_task(456)
    assert other_comic["status"] == "queued"

    db.expire_all()
    job = db.get(ScanJob, first["job_id"])
    assert job.job_type == JobType.EXTERNAL_REVIEW_LOOKUP
    assert job.target_type == "comic"
    assert job.target_id == 123
    assert job.library_id is None
    assert json.loads(job.payload_json) == {"provider": "comicbookroundup"}

    # Once the lookup finishes, a new one can be queued
    job.status = JobStatus.COMPLETED
    db.commit()
    assert manager.add_external_review_task(123)["status"] == "queued"


def test_review_lookups_and_library_jobs_do_not_block_each_other(monkeypatch, db):
    manager = _manager()

    scan_job = ScanJob(library_id=None, job_type=JobType.SCAN, status=JobStatus.PENDING)
    review_job = ScanJob(
        job_type=JobType.EXTERNAL_REVIEW_LOOKUP,
        target_type="comic",
        target_id=123,
        payload_json=json.dumps({"provider": "comicbookroundup"}),
        status=JobStatus.PENDING,
    )
    db.add_all([scan_job, review_job])
    db.commit()

    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))
    monkeypatch.setattr(sm.time, "sleep", MagicMock())
    manager._set_library_scanning_status = MagicMock()
    manager._run_scan_job = MagicMock()
    manager._run_external_review_job = MagicMock()

    # The enrichment lane claims the review lookup even though a scan is waiting
    manager._stop_event = MagicMock()
    manager._stop_event.is_set.side_effect = [False, True]
    manager._process_lane(ENRICHMENT_LANE)

    manager._run_scan_job.assert_not_called()
    manager._run_external_review_job.assert_called_once()
    job_data = manager._run_external_review_job.call_args.args[0]
    assert job_data["id"] == review_job.id
    assert job_data["target_id"] == 123
    assert json.loads(job_data["payload"]) == {"provider": "comicbookroundup"}
    manager._set_library_scanning_status.assert_not_called()

    # The library lane claims the scan and ignores review lookups
    manager._run_external_review_job.reset_mock()
    manager._stop_event.is_set.side_effect = [False, True]
    manager._process_lane(sm.LIBRARY_LANE)

    manager._run_scan_job.assert_called_once()
    manager._run_external_review_job.assert_not_called()


def test_run_external_review_job_completes_with_lookup_summary(monkeypatch):
    manager = _manager()
    result = {
        "provider": "comicbookroundup",
        "comic_id": 123,
        "issue_title": "Saga #1",
        "match_status": "matched",
        "confidence_score": 92.0,
        "review_count": 3,
        "source_issue_url": "https://comicbookroundup.com/comic-books/reviews/image-comics/saga/1",
        "error": None,
    }
    refresh = MagicMock(return_value=result)
    monkeypatch.setattr(sm, "refresh_external_reviews_for_comic", refresh)
    monkeypatch.setattr(sm, "get_cached_setting", lambda key, default: True)
    manager._safe_job_update = MagicMock()

    manager._run_external_review_job(_review_job_data())

    refresh.assert_called_once_with(123)
    manager._safe_job_update.assert_called_once_with(40, JobStatus.COMPLETED, summary=result, error=None)
    assert manager._last_external_review_at is not None


@pytest.mark.parametrize("match_status", ["no_match", "no_reviews", "ambiguous"])
def test_run_external_review_job_treats_negative_results_as_completed(monkeypatch, match_status):
    manager = _manager()
    monkeypatch.setattr(
        sm,
        "refresh_external_reviews_for_comic",
        MagicMock(return_value={"comic_id": 123, "match_status": match_status, "error": None}),
    )
    monkeypatch.setattr(sm, "get_cached_setting", lambda key, default: True)
    manager._safe_job_update = MagicMock()

    manager._run_external_review_job(_review_job_data())

    call = manager._safe_job_update.call_args
    assert call.args[1] == JobStatus.COMPLETED
    assert call.kwargs["error"] is None


def test_run_external_review_job_marks_failed_lookup_as_failed_job(monkeypatch):
    manager = _manager()
    monkeypatch.setattr(
        sm,
        "refresh_external_reviews_for_comic",
        MagicMock(return_value={"comic_id": 123, "match_status": "failed", "error": "timed out"}),
    )
    monkeypatch.setattr(sm, "get_cached_setting", lambda key, default: True)
    manager._safe_job_update = MagicMock()

    manager._run_external_review_job(_review_job_data())

    call = manager._safe_job_update.call_args
    assert call.args[1] == JobStatus.FAILED
    assert call.kwargs["error"] == "timed out"
    assert call.kwargs["summary"]["match_status"] == "failed"


def test_run_external_review_job_skips_without_lookup_when_disabled(monkeypatch):
    manager = _manager()
    refresh = MagicMock()
    monkeypatch.setattr(sm, "refresh_external_reviews_for_comic", refresh)
    monkeypatch.setattr(sm, "get_cached_setting", lambda key, default: False)
    manager._safe_job_update = MagicMock()

    manager._run_external_review_job(_review_job_data())

    refresh.assert_not_called()
    manager._safe_job_update.assert_called_once_with(
        40,
        JobStatus.COMPLETED,
        summary={"provider": "comicbookroundup", "comic_id": 123, "skipped": "External reviews are disabled"},
        error=None,
    )
    # No request was made, so the rate limit clock is untouched
    assert manager._last_external_review_at is None


def test_run_external_review_job_skips_deleted_comic(monkeypatch):
    manager = _manager()
    monkeypatch.setattr(sm, "refresh_external_reviews_for_comic", MagicMock(return_value=None))
    monkeypatch.setattr(sm, "get_cached_setting", lambda key, default: True)
    manager._safe_job_update = MagicMock()

    manager._run_external_review_job(_review_job_data())

    call = manager._safe_job_update.call_args
    assert call.args[1] == JobStatus.COMPLETED
    assert call.kwargs["summary"]["skipped"] == "Comic not found"


def test_run_external_review_job_marks_unexpected_error_failed(monkeypatch):
    manager = _manager()
    monkeypatch.setattr(
        sm,
        "refresh_external_reviews_for_comic",
        MagicMock(side_effect=RuntimeError("database is locked")),
    )
    monkeypatch.setattr(sm, "get_cached_setting", lambda key, default: True)
    manager._safe_job_update = MagicMock()

    manager._run_external_review_job(_review_job_data())

    call = manager._safe_job_update.call_args
    assert call.args[1] == JobStatus.FAILED
    assert call.kwargs["error"] == "database is locked"
    # An attempted request still counts toward the rate limit
    assert manager._last_external_review_at is not None


def test_wait_for_external_review_interval_spaces_out_lookups(monkeypatch):
    manager = _manager()
    manager._stop_event = MagicMock()

    # First lookup: nothing to wait for
    manager._wait_for_external_review_interval()
    manager._stop_event.wait.assert_not_called()

    # Back-to-back lookup waits out the rest of the interval on the stop event
    monkeypatch.setattr(sm.time, "monotonic", lambda: 101.0)
    manager._last_external_review_at = 100.0
    manager._wait_for_external_review_interval()
    manager._stop_event.wait.assert_called_once_with(
        pytest.approx(manager.EXTERNAL_REVIEW_MIN_INTERVAL_SECONDS - 1.0)
    )

    # Enough time has passed: no wait
    manager._stop_event.wait.reset_mock()
    monkeypatch.setattr(sm.time, "monotonic", lambda: 200.0)
    manager._wait_for_external_review_interval()
    manager._stop_event.wait.assert_not_called()


def test_recover_interrupted_jobs_uses_review_lookup_message(monkeypatch, db):
    manager = _manager()

    review_job = ScanJob(
        job_type=JobType.EXTERNAL_REVIEW_LOOKUP,
        target_type="comic",
        target_id=123,
        status=JobStatus.RUNNING,
    )
    db.add(review_job)
    db.commit()

    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))
    manager._recover_interrupted_jobs()

    db.refresh(review_job)
    assert review_job.status == JobStatus.FAILED
    assert review_job.error_message == "External review lookup interrupted by server restart"


def _statistics_exist(db) -> bool:
    return db.connection().exec_driver_sql("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_stat1'").first() is not None


def test_completed_cleanup_job_refreshes_database_statistics(monkeypatch, db):
    manager = _manager()
    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))
    maintenance = MagicMock()
    maintenance.cleanup_missing_files.return_value = []
    maintenance.cleanup_orphans.return_value = {}
    monkeypatch.setattr(sm, "MaintenanceService", MagicMock(return_value=maintenance))
    manager._safe_job_update = MagicMock()
    manager._set_library_scanning_status = MagicMock()

    manager._run_cleanup_job({"id": 30, "library_id": 5})

    assert _statistics_exist(db)


def test_failed_cleanup_job_does_not_refresh_database_statistics(monkeypatch, db):
    manager = _manager()
    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))
    maintenance = MagicMock()
    maintenance.cleanup_missing_files.side_effect = RuntimeError("cleanup fail")
    monkeypatch.setattr(sm, "MaintenanceService", MagicMock(return_value=maintenance))
    manager._safe_job_update = MagicMock()
    manager._set_library_scanning_status = MagicMock()
    manager._refresh_database_statistics = MagicMock()

    manager._run_cleanup_job({"id": 31, "library_id": 5})

    manager._refresh_database_statistics.assert_not_called()


@pytest.mark.parametrize("fatal_error", [None, "writer boom"])
def test_finished_scan_job_refreshes_database_statistics(monkeypatch, db, fatal_error):
    manager = _manager()
    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))
    lib = create_library_with_root(db, "stats-scan-lib", "/tmp/stats-scan-lib")
    db.commit()
    scanner_mock = MagicMock()
    scanner_mock.scan_parallel.return_value = {"imported": 1, "fatal_error": fatal_error}
    monkeypatch.setattr(sm, "LibraryScanner", lambda library, session: scanner_mock)
    monkeypatch.setattr(sm, "get_cached_setting", lambda key, default: False)
    manager._safe_job_update = MagicMock()

    manager._run_scan_job({"id": 32, "library_id": lib.id, "force": False})

    assert _statistics_exist(db)


def test_completed_metadata_rehydrate_job_refreshes_database_statistics(monkeypatch, db):
    manager = _manager()
    monkeypatch.setattr(sm, "SessionLocal", _session_local_factory(db))
    lib = create_library_with_root(db, "stats-rehydrate-lib", "/tmp/stats-rehydrate-lib")
    db.commit()
    monkeypatch.setattr(sm, "rehydrate_library_metadata_from_cache", MagicMock(return_value={"comics": 1}))
    manager._safe_job_update = MagicMock()
    manager._set_library_scanning_status = MagicMock()

    manager._run_metadata_rehydrate_job({"id": 33, "library_id": lib.id})

    assert _statistics_exist(db)


def test_statistics_refresh_failure_is_logged_not_raised(monkeypatch):
    manager = _manager()
    monkeypatch.setattr(sm, "refresh_query_planner_statistics", MagicMock(side_effect=RuntimeError("locked")))
    monkeypatch.setattr(sm, "SessionLocal", MagicMock())

    manager._refresh_database_statistics()

    manager.logger.warning.assert_called_once()
    assert "locked" in manager.logger.warning.call_args.args[0]
