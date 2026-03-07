from queue import Empty
from types import SimpleNamespace

import pytest

import app.services.scanner as scanner_module
from app.services.scanner import LibraryScanner


class DummyQuery:
    def __init__(self, existing):
        self._existing = existing

    def join(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._existing)


class DummyDB:
    def __init__(self, existing):
        self._existing = existing
        self.commit_calls = 0

    def query(self, *_args, **_kwargs):
        return DummyQuery(self._existing)

    def commit(self):
        self.commit_calls += 1


class FakeQueue:
    def __init__(self, initial_get_items=None):
        self.put_items = []
        self._get_items = list(initial_get_items or [])

    def put(self, item):
        self.put_items.append(item)

    def get(self, timeout=None):
        if not self._get_items:
            raise AssertionError("Queue.get() called with no prepared values")
        return self._get_items.pop(0)


class FakeProcess:
    instances = []
    force_stuck = False

    def __init__(self, target=None, args=None, kwargs=None):
        self.target = target
        self.args = args or ()
        self.kwargs = kwargs or {}
        self.started = False
        self.joined = False
        self.terminated = False
        self.alive = False
        FakeProcess.instances.append(self)

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


class FakePool:
    last_processes = None

    def __init__(self, processes):
        self.processes = processes
        FakePool.last_processes = processes

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def imap_unordered(self, worker_fn, tasks):
        for task in reversed(tasks):
            yield worker_fn(task)


class TimeoutQueue(FakeQueue):
    def get(self, timeout=None):
        raise Empty()


def fake_worker(file_path):
    return {
        "file_path": file_path,
        "mtime": 1.0,
        "size": 123,
        "metadata": {"page_count": 1, "raw_metadata": {}},
        "error": False,
    }


def _create_file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def _disable_container_cleanup(scanner):
    scanner.reading_list_service.cleanup_empty_lists = lambda: None
    scanner.collection_service.cleanup_empty_collections = lambda: None


def test_scan_parallel_orchestrates_pool_writer_and_summary(monkeypatch, tmp_path):
    library_path = tmp_path / "library"
    unchanged = _create_file(library_path / "unchanged.cbz")
    changed = _create_file(library_path / "changed.cbz")
    _create_file(library_path / "new.cbz")

    existing = [
        SimpleNamespace(file_path=str(unchanged), file_modified_at=unchanged.stat().st_mtime + 30),
        SimpleNamespace(file_path=str(changed), file_modified_at=changed.stat().st_mtime - 30),
    ]

    db = DummyDB(existing)
    library = SimpleNamespace(path=str(library_path), name="Test", id=42, last_scanned=None)
    scanner = LibraryScanner(library, db)

    scanner._reconcile_sidecars = lambda *_args, **_kwargs: None
    scanner._cleanup_missing_files = lambda *_args, **_kwargs: 0
    _disable_container_cleanup(scanner)

    result_queue = FakeQueue()
    stats_queue = FakeQueue([
        {"summary": True, "imported": 2, "updated": 0, "errors": 0, "skipped": 0}
    ])
    queues = [result_queue, stats_queue]

    FakeProcess.instances = []
    FakePool.last_processes = None

    monkeypatch.setattr(scanner_module, "Queue", lambda: queues.pop(0))
    monkeypatch.setattr(scanner_module, "metadata_worker", fake_worker)
    monkeypatch.setattr(scanner_module.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(scanner_module.multiprocessing, "Pool", FakePool)
    monkeypatch.setattr(scanner_module, "get_cached_setting", lambda _key, default=0: default)

    result = scanner.scan_parallel(force=False, worker_limit=3)

    assert result["skipped"] == 1
    assert result["imported"] == 2
    assert result["updated"] == 0
    assert result["errors"] == 0

    assert len(FakeProcess.instances) == 1
    assert FakeProcess.instances[0].started is True
    assert FakeProcess.instances[0].joined is True

    assert FakePool.last_processes == 3

    queued_payloads = [item for item in result_queue.put_items if item is not None]
    assert len(queued_payloads) == 2
    assert result_queue.put_items[-1] is None


def test_scan_parallel_no_tasks_skips_parallel_components(monkeypatch, tmp_path):
    library_path = tmp_path / "library"
    unchanged = _create_file(library_path / "only.cbz")

    existing = [
        SimpleNamespace(file_path=str(unchanged), file_modified_at=unchanged.stat().st_mtime + 30),
    ]

    db = DummyDB(existing)
    library = SimpleNamespace(path=str(library_path), name="Test", id=7, last_scanned=None)
    scanner = LibraryScanner(library, db)

    scanner._reconcile_sidecars = lambda *_args, **_kwargs: None
    scanner._cleanup_missing_files = lambda *_args, **_kwargs: 0
    _disable_container_cleanup(scanner)

    def _should_not_start(*_args, **_kwargs):
        raise AssertionError("Parallel components should not start when there are no tasks")

    monkeypatch.setattr(scanner_module.multiprocessing, "Process", _should_not_start)
    monkeypatch.setattr(scanner_module.multiprocessing, "Pool", _should_not_start)
    monkeypatch.setattr(scanner_module, "get_cached_setting", lambda _key, default=0: default)

    result = scanner.scan_parallel(force=False, worker_limit=2)

    assert result["skipped"] == 1
    assert result["imported"] == 0
    assert result["updated"] == 0
    assert result["errors"] == 0


def test_scan_parallel_auto_worker_count_uses_half_cores(monkeypatch, tmp_path):
    library_path = tmp_path / "library"
    _create_file(library_path / "new.cbz")

    db = DummyDB(existing=[])
    library = SimpleNamespace(path=str(library_path), name="Test", id=99, last_scanned=None)
    scanner = LibraryScanner(library, db)

    scanner._reconcile_sidecars = lambda *_args, **_kwargs: None
    scanner._cleanup_missing_files = lambda *_args, **_kwargs: 0
    _disable_container_cleanup(scanner)

    result_queue = FakeQueue()
    stats_queue = FakeQueue([
        {"summary": True, "imported": 1, "updated": 0, "errors": 0, "skipped": 0}
    ])
    queues = [result_queue, stats_queue]

    FakePool.last_processes = None
    FakeProcess.instances = []

    monkeypatch.setattr(scanner_module, "Queue", lambda: queues.pop(0))
    monkeypatch.setattr(scanner_module, "metadata_worker", fake_worker)
    monkeypatch.setattr(scanner_module.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(scanner_module.multiprocessing, "Pool", FakePool)
    monkeypatch.setattr(scanner_module.multiprocessing, "cpu_count", lambda: 8)
    monkeypatch.setattr(scanner_module, "get_cached_setting", lambda *_args, **_kwargs: 0)

    result = scanner.scan_parallel(force=False, worker_limit=0)

    assert result["imported"] == 1
    assert FakePool.last_processes == 4


def test_scan_parallel_timeout_terminates_stuck_writer(monkeypatch, tmp_path):
    library_path = tmp_path / "library"
    _create_file(library_path / "new.cbz")

    db = DummyDB(existing=[])
    library = SimpleNamespace(path=str(library_path), name="Test", id=100, last_scanned=None)
    scanner = LibraryScanner(library, db)

    scanner._reconcile_sidecars = lambda *_args, **_kwargs: None
    scanner._cleanup_missing_files = lambda *_args, **_kwargs: 0
    _disable_container_cleanup(scanner)

    result_queue = FakeQueue()
    stats_queue = TimeoutQueue()
    queues = [result_queue, stats_queue]

    FakePool.last_processes = None
    FakeProcess.instances = []
    FakeProcess.force_stuck = True

    monkeypatch.setattr(scanner_module, "Queue", lambda: queues.pop(0))
    monkeypatch.setattr(scanner_module, "metadata_worker", fake_worker)
    monkeypatch.setattr(scanner_module.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(scanner_module.multiprocessing, "Pool", FakePool)
    monkeypatch.setattr(
        scanner_module,
        "get_cached_setting",
        lambda key, default=0: 10 if "timeout" in key else default,
    )

    with pytest.raises(TimeoutError):
        scanner.scan_parallel(force=False, worker_limit=1)

    writer_proc = FakeProcess.instances[0]
    assert writer_proc.joined is True
    assert writer_proc.terminated is True
    assert result_queue.put_items[-1] is None

    FakeProcess.force_stuck = False


def test_scan_parallel_pool_error_still_signals_and_joins_writer(monkeypatch, tmp_path):
    library_path = tmp_path / "library"
    _create_file(library_path / "new.cbz")

    db = DummyDB(existing=[])
    library = SimpleNamespace(path=str(library_path), name="Test", id=101, last_scanned=None)
    scanner = LibraryScanner(library, db)

    scanner._reconcile_sidecars = lambda *_args, **_kwargs: None
    scanner._cleanup_missing_files = lambda *_args, **_kwargs: 0
    _disable_container_cleanup(scanner)

    result_queue = FakeQueue()
    stats_queue = FakeQueue([])
    queues = [result_queue, stats_queue]

    FakeProcess.instances = []
    FakeProcess.force_stuck = False

    class ExplodingPool(FakePool):
        def imap_unordered(self, worker_fn, tasks):
            raise RuntimeError("pool exploded")

    monkeypatch.setattr(scanner_module, "Queue", lambda: queues.pop(0))
    monkeypatch.setattr(scanner_module, "metadata_worker", fake_worker)
    monkeypatch.setattr(scanner_module.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(scanner_module.multiprocessing, "Pool", ExplodingPool)
    monkeypatch.setattr(scanner_module, "get_cached_setting", lambda _key, default=0: default)

    with pytest.raises(RuntimeError, match="pool exploded"):
        scanner.scan_parallel(force=False, worker_limit=2)

    writer_proc = FakeProcess.instances[0]
    assert writer_proc.joined is True
    assert writer_proc.terminated is False
    assert result_queue.put_items[-1] is None

