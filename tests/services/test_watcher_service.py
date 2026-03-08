from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import app.services.watcher as watcher


class DummyTimer:
    def __init__(self, interval, callback):
        self.interval = interval
        self.callback = callback
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


def _watcher_instance():
    inst = object.__new__(watcher.LibraryWatcher)
    inst.logger = MagicMock()
    inst.observer = MagicMock()
    inst.watches = {}
    inst.is_running = False
    inst._initialized = True
    return inst


def test_library_event_handler_stop_cancels_pending_timer():
    handler = watcher.LibraryEventHandler(11, batch_window_seconds=10)
    timer = MagicMock()
    handler._timer = timer

    handler.stop()

    assert handler._stopped is True
    timer.cancel.assert_called_once()
    assert handler._timer is None


def test_library_event_handler_trigger_scan_stopped_skips_queue(monkeypatch):
    handler = watcher.LibraryEventHandler(11, batch_window_seconds=10)
    handler._stopped = True

    add_task = MagicMock()
    monkeypatch.setattr(watcher.scan_manager, "add_task", add_task)

    handler._trigger_scan()

    add_task.assert_not_called()


def test_library_event_handler_trigger_scan_active_queues(monkeypatch):
    handler = watcher.LibraryEventHandler(22, batch_window_seconds=10)
    handler._timer = object()

    add_task = MagicMock()
    monkeypatch.setattr(watcher.scan_manager, "add_task", add_task)

    handler._trigger_scan()

    assert handler._timer is None
    add_task.assert_called_once_with(22, force=False)


def test_library_event_handler_on_any_event_filters_and_batches(monkeypatch):
    timer_ctor = MagicMock(side_effect=lambda interval, callback: DummyTimer(interval, callback))
    monkeypatch.setattr(watcher.threading, "Timer", timer_ctor)

    handler = watcher.LibraryEventHandler(33, batch_window_seconds=30)

    directory_event = SimpleNamespace(is_directory=True, src_path="/lib/folder", event_type="modified")
    ignored_ext = SimpleNamespace(is_directory=False, src_path="/lib/issue.tmp", event_type="created")
    ignored_name = SimpleNamespace(is_directory=False, src_path="/lib/thumbs.db", event_type="modified")
    ignored_dir = SimpleNamespace(is_directory=False, src_path="/lib/storage/issue.cbz", event_type="moved")
    good_event = SimpleNamespace(is_directory=False, src_path="/lib/comics/issue.cbz", event_type="modified")

    handler.on_any_event(directory_event)
    handler.on_any_event(ignored_ext)
    handler.on_any_event(ignored_name)
    handler.on_any_event(ignored_dir)
    handler.on_any_event(good_event)
    handler.on_any_event(good_event)

    timer_ctor.assert_called_once()
    created_timer = handler._timer
    assert isinstance(created_timer, DummyTimer)
    assert created_timer.interval == 30
    assert created_timer.started is True


def test_library_event_handler_on_any_event_stopped_does_not_start_timer(monkeypatch):
    timer_ctor = MagicMock()
    monkeypatch.setattr(watcher.threading, "Timer", timer_ctor)

    handler = watcher.LibraryEventHandler(44, batch_window_seconds=20)
    handler._stopped = True

    event = SimpleNamespace(is_directory=False, src_path="/lib/comics/new.cbz", event_type="created")
    handler.on_any_event(event)

    timer_ctor.assert_not_called()


def test_library_watcher_singleton_new_and_init_guard(monkeypatch):
    monkeypatch.setattr(watcher.LibraryWatcher, "_instance", None)

    fake_observer = MagicMock()
    observer_ctor = MagicMock(return_value=fake_observer)
    monkeypatch.setattr(watcher, "Observer", observer_ctor)

    first = watcher.LibraryWatcher()
    second = watcher.LibraryWatcher()

    assert first is second
    assert first.observer is fake_observer
    observer_ctor.assert_called_once()

    keep = object()
    first.observer = keep
    watcher.LibraryWatcher.__init__(first)
    assert first.observer is keep


def test_library_watcher_start_and_stop_only_once():
    inst = _watcher_instance()
    inst.refresh_watches = MagicMock()

    inst.start()
    inst.start()

    inst.refresh_watches.assert_called_once()
    inst.observer.start.assert_called_once()
    assert inst.is_running is True

    inst.stop()
    inst.stop()

    inst.observer.stop.assert_called_once()
    inst.observer.join.assert_called_once()
    assert inst.is_running is False


def test_refresh_watches_adds_and_removes_and_closes_session(monkeypatch):
    inst = _watcher_instance()

    old_watch = object()
    old_handler = MagicMock()
    inst.watches = {1: (old_watch, old_handler)}

    lib = SimpleNamespace(id=2, path="/library/two")

    db = MagicMock()
    q = db.query.return_value
    q.filter.return_value = q
    q.all.return_value = [lib]
    monkeypatch.setattr(watcher, "SessionLocal", lambda: db)

    monkeypatch.setattr(watcher, "get_cached_setting", lambda key, default: "45")

    new_handler = MagicMock()
    handler_ctor = MagicMock(return_value=new_handler)
    monkeypatch.setattr(watcher, "LibraryEventHandler", handler_ctor)

    inst.observer.schedule.return_value = "new-watch"

    inst.refresh_watches()

    handler_ctor.assert_called_once_with(2, 45)
    inst.observer.schedule.assert_called_once_with(new_handler, "/library/two", recursive=True)
    old_handler.stop.assert_called_once()
    inst.observer.unschedule.assert_called_once_with(old_watch)
    assert 1 not in inst.watches
    assert 2 in inst.watches
    db.close.assert_called_once()


def test_refresh_watches_keeps_existing_watch_without_reschedule(monkeypatch):
    inst = _watcher_instance()
    keep_watch = object()
    keep_handler = MagicMock()
    inst.watches = {9: (keep_watch, keep_handler)}

    db = MagicMock()
    q = db.query.return_value
    q.filter.return_value = q
    q.all.return_value = [SimpleNamespace(id=9, path="/library/nine")]

    monkeypatch.setattr(watcher, "SessionLocal", lambda: db)
    monkeypatch.setattr(watcher, "get_cached_setting", lambda key, default: 600)

    inst.refresh_watches()

    inst.observer.schedule.assert_not_called()
    inst.observer.unschedule.assert_not_called()
    keep_handler.stop.assert_not_called()
    assert 9 in inst.watches


def test_refresh_watches_logs_schedule_errors(monkeypatch):
    inst = _watcher_instance()

    db = MagicMock()
    q = db.query.return_value
    q.filter.return_value = q
    q.all.return_value = [SimpleNamespace(id=12, path="/library/bad")]

    monkeypatch.setattr(watcher, "SessionLocal", lambda: db)
    monkeypatch.setattr(watcher, "get_cached_setting", lambda key, default: 10)
    monkeypatch.setattr(watcher, "LibraryEventHandler", MagicMock(return_value=MagicMock()))
    inst.observer.schedule.side_effect = RuntimeError("schedule failed")

    inst.refresh_watches()

    assert 12 not in inst.watches
    inst.logger.error.assert_called()
    db.close.assert_called_once()


def test_refresh_watches_closes_session_when_query_raises(monkeypatch):
    inst = _watcher_instance()

    db = MagicMock()
    db.query.side_effect = RuntimeError("db broke")
    monkeypatch.setattr(watcher, "SessionLocal", lambda: db)

    with pytest.raises(RuntimeError, match="db broke"):
        inst.refresh_watches()

    db.close.assert_called_once()
