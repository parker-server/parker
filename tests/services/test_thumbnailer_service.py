from pathlib import Path
from queue import Empty
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models.comic import Volume
from app.models.series import Series
from app.services.thumbnailer import (
    ThumbnailService,
    _apply_batch,
    _thumbnail_worker,
    _thumbnail_writer,
)
import app.services.thumbnailer as thumbnailer_module
from tests.factories import create_comic, create_library_with_root


class _ReadQueue:
    def __init__(self, items):
        self.items = list(items)

    def get(self):
        if not self.items:
            raise AssertionError("queue unexpectedly empty")
        return self.items.pop(0)


class _WriteQueue:
    def __init__(self, initial_get_items=None):
        self.get_items = list(initial_get_items or [])
        self.put_items = []

    def put(self, item):
        self.put_items.append(item)

    def get(self, timeout=None):
        if not self.get_items:
            raise AssertionError("queue unexpectedly empty")
        return self.get_items.pop(0)


class _TimeoutQueue(_WriteQueue):
    def get(self, timeout=None):
        raise Empty()


class _FakeProcess:
    instances = []
    force_stuck = False

    def __init__(self, target=None, args=(), kwargs=None):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.started = False
        self.joined = False
        self.terminated = False
        self.alive = False
        _FakeProcess.instances.append(self)

    def start(self):
        self.started = True
        self.alive = True

    def join(self, timeout=None):
        self.joined = True
        if not self.force_stuck:
            self.alive = False

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.terminated = True
        self.alive = False


class _FakePool:
    created_processes = []

    def __init__(self, processes):
        self.processes = processes
        _FakePool.created_processes.append(processes)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def imap_unordered(self, fn, tasks):
        for comic_id, _file_path in tasks:
            yield {
                "comic_id": comic_id,
                "thumbnail_path": f"./storage/cover/comic_{comic_id}.webp",
                "palette": {"primary": "#111", "secondary": "#222"},
                "error": False,
            }


def _seed_library_and_comics(db, tmp_path, *, lib_name="thumb-lib"):
    lib = create_library_with_root(db, lib_name, str(tmp_path / lib_name))

    series = Series(name=f"{lib_name}-series", library_id=lib.id)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()

    return lib, series, volume


def test_apply_batch_updates_processed_and_reports_error_and_missing(db, tmp_path):
    lib, _series, volume = _seed_library_and_comics(db, tmp_path, lib_name="thumb-batch-lib")
    root = lib.active_root

    comic_with_palette = create_comic(
        db, volume, root, "one.cbz",
        number="1",
        filename="one.cbz",
        page_count=10,
        is_dirty=True,
    )
    comic_no_palette = create_comic(
        db, volume, root, "two.cbz",
        number="2",
        filename="two.cbz",
        page_count=12,
        is_dirty=True,
    )
    db.commit()

    outcomes = _apply_batch(
        db,
        [
            {
                "comic_id": comic_with_palette.id,
                "thumbnail_path": "./storage/cover/comic_1.webp",
                "palette": {"primary": "#111", "secondary": "#222", "all": ["#111", "#222"]},
            },
            {
                "comic_id": comic_no_palette.id,
                "thumbnail_path": "./storage/cover/comic_2.webp",
                "palette": None,
            },
            {"comic_id": 999999, "thumbnail_path": "./storage/cover/missing.webp", "palette": None},
            {"comic_id": 123456, "error": True},
        ],
    )

    db.refresh(comic_with_palette)
    db.refresh(comic_no_palette)

    assert comic_with_palette.thumbnail_path.endswith("comic_1.webp")
    assert comic_with_palette.color_primary == "#111"
    assert comic_with_palette.color_secondary == "#222"
    assert comic_with_palette.color_palette["all"] == ["#111", "#222"]
    assert comic_with_palette.is_dirty is False

    assert comic_no_palette.thumbnail_path.endswith("comic_2.webp")
    assert comic_no_palette.color_primary is None
    assert comic_no_palette.color_secondary is None
    assert comic_no_palette.color_palette is None
    assert comic_no_palette.is_dirty is False

    statuses = {outcome["status"] for outcome in outcomes}
    assert statuses == {"processed", "missing", "error"}
    assert sum(1 for outcome in outcomes if outcome["detail"] is not None) == 2


@pytest.mark.parametrize(
    "result_payload,expect_error",
    [
        ({"success": True, "palette": {"primary": "#abc"}}, False),
        ({"success": False}, True),
    ],
)
def test_thumbnail_worker_success_and_failure_paths(monkeypatch, result_payload, expect_error):
    class FakeImageService:
        def process_cover(self, file_path, target_path):
            return result_payload

    monkeypatch.setattr("app.services.images.ImageService", FakeImageService)

    payload = _thumbnail_worker((42, "/tmp/test.cbz"))

    assert payload["comic_id"] == 42
    assert payload["error"] is expect_error
    if expect_error:
        assert payload["message"] == "Image processing failed"
    else:
        assert payload["thumbnail_path"].endswith("comic_42.webp")
        assert payload["palette"]["primary"] == "#abc"


def test_thumbnail_worker_exception_path(monkeypatch):
    class FakeImageService:
        def process_cover(self, file_path, target_path):
            raise RuntimeError("explode")

    monkeypatch.setattr("app.services.images.ImageService", FakeImageService)

    payload = _thumbnail_worker((7, "/tmp/test.cbz"))

    assert payload["comic_id"] == 7
    assert payload["error"] is True
    assert "explode" in payload["message"]


def test_thumbnail_writer_batches_and_sends_summary(monkeypatch):
    applied_batches = []

    def fake_apply_batch_with_retry(db, batch):
        applied_batches.append(list(batch))
        return [
            {
                "comic_id": item["comic_id"],
                "status": "error" if item.get("error") else "processed",
                "detail": (
                    {"comic_id": item["comic_id"], "file_path": None, "message": "boom"}
                    if item.get("error") else None
                ),
            }
            for item in batch
        ]

    class SummaryQueue:
        def __init__(self):
            self.items = []

        def put(self, item):
            self.items.append(item)

    monkeypatch.setattr(thumbnailer_module.engine, "dispose", MagicMock())
    monkeypatch.setattr(thumbnailer_module, "_apply_batch_with_retry", fake_apply_batch_with_retry)

    result_queue = _ReadQueue(
        [
            {"comic_id": 1, "error": False},
            {"comic_id": 2, "error": True},
            {"comic_id": 3, "error": False},
            None,
        ]
    )
    stats_queue = SummaryQueue()

    _thumbnail_writer(result_queue, stats_queue, batch_size=2)

    assert len(applied_batches) == 2
    assert [len(batch) for batch in applied_batches] == [2, 1]

    summary = stats_queue.items[-1]
    assert summary["summary"] is True
    assert summary["processed"] == 2
    assert summary["errors"] == 1
    assert summary["skipped"] == 0
    assert summary["error_details"] == [{"comic_id": 2, "file_path": None, "message": "boom"}]


def test_apply_batch_with_retry_recovers_from_transient_lock(monkeypatch):
    from sqlalchemy.exc import OperationalError

    calls = {"n": 0}

    def fake_apply_batch(db, batch):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("stmt", {}, Exception("database is locked"))
        return [{"comic_id": 1, "status": "processed", "detail": None}]

    monkeypatch.setattr(thumbnailer_module, "_apply_batch", fake_apply_batch)
    monkeypatch.setattr(thumbnailer_module.time, "sleep", MagicMock())

    db = MagicMock()
    result = thumbnailer_module._apply_batch_with_retry(db, [{"comic_id": 1}], attempts=3, delay=0)

    assert result == [{"comic_id": 1, "status": "processed", "detail": None}]
    assert calls["n"] == 2
    db.rollback.assert_called_once()


def test_apply_batch_with_retry_gives_up_after_max_attempts(monkeypatch):
    from sqlalchemy.exc import OperationalError

    def always_locked(db, batch):
        raise OperationalError("stmt", {}, Exception("database is locked"))

    monkeypatch.setattr(thumbnailer_module, "_apply_batch", always_locked)
    monkeypatch.setattr(thumbnailer_module.time, "sleep", MagicMock())

    db = MagicMock()
    with pytest.raises(OperationalError):
        thumbnailer_module._apply_batch_with_retry(db, [{"comic_id": 1}], attempts=3, delay=0)

    assert db.rollback.call_count == 3


def test_apply_batch_with_retry_reraises_non_lock_error_immediately(monkeypatch):
    from sqlalchemy.exc import OperationalError

    def other_error(db, batch):
        raise OperationalError("stmt", {}, Exception("no such table: foo"))

    monkeypatch.setattr(thumbnailer_module, "_apply_batch", other_error)
    sleep_mock = MagicMock()
    monkeypatch.setattr(thumbnailer_module.time, "sleep", sleep_mock)

    db = MagicMock()
    with pytest.raises(OperationalError):
        thumbnailer_module._apply_batch_with_retry(db, [{"comic_id": 1}], attempts=5, delay=0)

    assert db.rollback.call_count == 1
    sleep_mock.assert_not_called()


def test_thumbnail_writer_reports_failure_when_batch_retries_exhausted(monkeypatch):
    from sqlalchemy.exc import OperationalError

    def always_fails(db, batch):
        raise OperationalError("stmt", {}, Exception("database is locked"))

    class SummaryQueue:
        def __init__(self):
            self.items = []

        def put(self, item):
            self.items.append(item)

    monkeypatch.setattr(thumbnailer_module.engine, "dispose", MagicMock())
    monkeypatch.setattr(thumbnailer_module, "_apply_batch_with_retry", always_fails)

    result_queue = _ReadQueue(
        [
            {"comic_id": 1, "error": False},
            {"comic_id": 2, "error": False},
            None,
        ]
    )
    stats_queue = SummaryQueue()

    _thumbnail_writer(result_queue, stats_queue, batch_size=2)

    summary = stats_queue.items[-1]
    assert summary["summary"] is True
    assert summary["processed"] == 0
    assert summary["errors"] == 2
    assert len(summary["error_details"]) == 1
    assert "Thumbnail writer aborted" in summary["error_details"][0]["message"]


def test_process_series_thumbnails_delegates(db, tmp_path, monkeypatch):
    service = ThumbnailService(db, library_id=55)
    delegated = MagicMock(return_value={"processed": 1, "errors": 0, "skipped": 0})
    monkeypatch.setattr(service, "process_missing_thumbnails_parallel", delegated)

    result = service.process_series_thumbnails(series_id=99)

    assert result["processed"] == 1
    delegated.assert_called_once_with(force=True, series_id=99, worker_limit=1)


def test_get_target_comics_requires_library_and_filters_dirty(db, tmp_path):
    service = ThumbnailService(db, library_id=None)
    with pytest.raises(ValueError, match="Library ID required"):
        service._get_target_comics()

    lib_a, _series_a, volume_a = _seed_library_and_comics(db, tmp_path, lib_name="target-lib-a")
    lib_b, _series_b, volume_b = _seed_library_and_comics(db, tmp_path, lib_name="target-lib-b")
    root_a = lib_a.active_root
    root_b = lib_b.active_root

    dirty_a = create_comic(
        db, volume_a, root_a, "dirty-a.cbz",
        number="1",
        filename="dirty-a.cbz",
        page_count=10,
        is_dirty=True,
    )
    clean_a = create_comic(
        db, volume_a, root_a, "clean-a.cbz",
        number="2",
        filename="clean-a.cbz",
        page_count=10,
        is_dirty=False,
    )
    dirty_b = create_comic(
        db, volume_b, root_b, "dirty-b.cbz",
        number="1",
        filename="dirty-b.cbz",
        page_count=10,
        is_dirty=True,
    )
    db.commit()

    scoped_service = ThumbnailService(db, library_id=lib_a.id)

    dirty_only = scoped_service._get_target_comics(force=False)
    assert {comic.id for comic in dirty_only} == {dirty_a.id}

    all_scoped = scoped_service._get_target_comics(force=True)
    assert {comic.id for comic in all_scoped} == {dirty_a.id, clean_a.id}


def test_process_missing_thumbnails_parallel_requires_target(db):
    service = ThumbnailService(db, library_id=None)
    with pytest.raises(ValueError, match="Either series_id OR initialized library_id is required"):
        service.process_missing_thumbnails_parallel()


def test_process_missing_thumbnails_parallel_returns_zero_when_no_comics(db, monkeypatch):
    service = ThumbnailService(db, library_id=1)
    monkeypatch.setattr(service, "_get_target_comics", lambda force=False: [])

    assert service.process_missing_thumbnails_parallel(force=False) == {"processed": 0, "errors": 0, "skipped": 0}


def test_process_missing_thumbnails_parallel_all_skipped_short_circuit(db, tmp_path, monkeypatch):
    service = ThumbnailService(db, library_id=10)

    thumb = tmp_path / "cover.webp"
    thumb.write_bytes(b"ok")

    skipped_comic = SimpleNamespace(
        id=1,
        absolute_path=str(tmp_path / "comic.cbz"),
        thumbnail_path=str(thumb),
        color_primary="#111",
        is_dirty=False,
    )

    monkeypatch.setattr(service, "_get_target_comics", lambda force=False: [skipped_comic])

    queue_factory = MagicMock()
    monkeypatch.setattr(thumbnailer_module.multiprocessing, "Queue", queue_factory)

    stats = service.process_missing_thumbnails_parallel(force=False)

    assert stats == {"processed": 0, "errors": 0, "skipped": 1}
    assert queue_factory.call_count == 0


@pytest.mark.parametrize("worker_limit,expected_workers", [(1, 1), (3, 3)])
def test_process_missing_thumbnails_parallel_worker_limit_override(db, tmp_path, monkeypatch, worker_limit, expected_workers):
    service = ThumbnailService(db, library_id=25)

    dirty = SimpleNamespace(
        id=5,
        absolute_path=str(tmp_path / "dirty.cbz"),
        thumbnail_path=None,
        color_primary=None,
        is_dirty=True,
    )
    monkeypatch.setattr(service, "_get_target_comics", lambda force=False: [dirty])

    result_queue = _WriteQueue()
    stats_queue = _WriteQueue(initial_get_items=[{"summary": True, "processed": 1, "errors": 0, "skipped": 0}])
    monkeypatch.setattr(thumbnailer_module.multiprocessing, "Queue", MagicMock(side_effect=[result_queue, stats_queue]))
    monkeypatch.setattr(thumbnailer_module.multiprocessing, "Process", _FakeProcess)
    monkeypatch.setattr(thumbnailer_module, "get_cached_setting", lambda _key, default=0: default)

    _FakePool.created_processes.clear()
    monkeypatch.setattr(thumbnailer_module.multiprocessing, "Pool", _FakePool)

    stats = service.process_missing_thumbnails_parallel(force=False, worker_limit=worker_limit)

    assert stats == {"processed": 1, "errors": 0, "skipped": 0, "error_details": []}
    assert _FakePool.created_processes[-1] == expected_workers
    assert result_queue.put_items[-1] is None


def test_process_missing_thumbnails_parallel_auto_worker_count_for_series(db, tmp_path, monkeypatch):
    lib, series, volume = _seed_library_and_comics(db, tmp_path, lib_name="auto-worker-lib")
    comic = create_comic(
        db, volume, lib.active_root, "auto.cbz",
        number="1",
        filename="auto.cbz",
        page_count=10,
        is_dirty=True,
    )
    db.commit()

    service = ThumbnailService(db, library_id=None)

    result_queue = _WriteQueue()
    stats_queue = _WriteQueue(initial_get_items=[{"summary": True, "processed": 1, "errors": 0, "skipped": 0}])
    monkeypatch.setattr(thumbnailer_module.multiprocessing, "Queue", MagicMock(side_effect=[result_queue, stats_queue]))
    monkeypatch.setattr(thumbnailer_module.multiprocessing, "Process", _FakeProcess)

    _FakePool.created_processes.clear()
    monkeypatch.setattr(thumbnailer_module.multiprocessing, "Pool", _FakePool)

    monkeypatch.setattr(thumbnailer_module, "get_cached_setting", lambda key, default: 0)
    monkeypatch.setattr(thumbnailer_module.multiprocessing, "cpu_count", lambda: 8)

    stats = service.process_missing_thumbnails_parallel(series_id=series.id, worker_limit=0)

    assert stats == {"processed": 1, "errors": 0, "skipped": 0, "error_details": []}
    assert _FakePool.created_processes[-1] == 4


def test_process_missing_thumbnails_parallel_respects_requested_worker_cap(db, tmp_path, monkeypatch):
    service = ThumbnailService(db, library_id=88)

    dirty = SimpleNamespace(
        id=9,
        absolute_path=str(tmp_path / "dirty2.cbz"),
        thumbnail_path=None,
        color_primary=None,
        is_dirty=True,
    )
    monkeypatch.setattr(service, "_get_target_comics", lambda force=False: [dirty])

    result_queue = _WriteQueue()
    stats_queue = _WriteQueue(initial_get_items=[{"summary": True, "processed": 1, "errors": 0, "skipped": 0}])
    monkeypatch.setattr(thumbnailer_module.multiprocessing, "Queue", MagicMock(side_effect=[result_queue, stats_queue]))
    monkeypatch.setattr(thumbnailer_module.multiprocessing, "Process", _FakeProcess)

    _FakePool.created_processes.clear()
    monkeypatch.setattr(thumbnailer_module.multiprocessing, "Pool", _FakePool)

    monkeypatch.setattr(thumbnailer_module, "get_cached_setting", lambda key, default: 99)
    monkeypatch.setattr(thumbnailer_module.multiprocessing, "cpu_count", lambda: 4)

    stats = service.process_missing_thumbnails_parallel(force=False, worker_limit=0)

    assert stats == {"processed": 1, "errors": 0, "skipped": 0, "error_details": []}
    assert _FakePool.created_processes[-1] == 4


def test_process_missing_thumbnails_parallel_timeout_terminates_stuck_writer(db, tmp_path, monkeypatch):
    service = ThumbnailService(db, library_id=200)

    dirty = SimpleNamespace(
        id=11,
        absolute_path=str(tmp_path / "stuck.cbz"),
        thumbnail_path=None,
        color_primary=None,
        is_dirty=True,
    )
    monkeypatch.setattr(service, "_get_target_comics", lambda force=False: [dirty])

    result_queue = _WriteQueue()
    stats_queue = _TimeoutQueue()
    monkeypatch.setattr(thumbnailer_module.multiprocessing, "Queue", MagicMock(side_effect=[result_queue, stats_queue]))
    monkeypatch.setattr(thumbnailer_module.multiprocessing, "Process", _FakeProcess)

    _FakePool.created_processes.clear()
    monkeypatch.setattr(thumbnailer_module.multiprocessing, "Pool", _FakePool)

    monkeypatch.setattr(
        thumbnailer_module,
        "get_cached_setting",
        lambda key, default=0: 10 if "timeout" in key else default,
    )

    _FakeProcess.instances = []
    _FakeProcess.force_stuck = True
    try:
        with pytest.raises(TimeoutError):
            service.process_missing_thumbnails_parallel(force=False, worker_limit=1)

        writer_proc = _FakeProcess.instances[0]
        assert writer_proc.joined is True
        assert writer_proc.terminated is True
        assert result_queue.put_items[-1] is None
    finally:
        _FakeProcess.force_stuck = False


