import warnings
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from queue import Empty
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image, ImageDraw

from app.config import settings
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.services.thumbnailer import (
    ThumbnailService,
    _apply_batch,
    _thumbnail_task,
    _thumbnail_worker,
    _thumbnail_writer,
)
import app.services.thumbnailer as thumbnailer_module
import app.services.workers.metadata_writer as metadata_writer_module
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
        for comic_id, *_rest in tasks:
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
        def process_cover(self, file_path, target_path, **_overrides):
            return result_payload

    monkeypatch.setattr("app.services.images.ImageService", FakeImageService)

    payload = _thumbnail_worker((42, "/tmp/test.cbz", None, None))

    assert payload["comic_id"] == 42
    assert payload["error"] is expect_error
    if expect_error:
        assert payload["message"] == "Image processing failed"
    else:
        assert payload["thumbnail_path"].endswith("comic_42.webp")
        assert payload["palette"]["primary"] == "#abc"


def test_thumbnail_worker_captures_image_warnings(monkeypatch):
    class FakeImageService:
        def process_cover(self, file_path, target_path, **_overrides):
            warnings.warn("Corrupt EXIF data. Expecting to read 2 bytes but only got 0.")
            return {"success": True, "palette": {"primary": "#abc"}}

    monkeypatch.setattr("app.services.images.ImageService", FakeImageService)

    payload = _thumbnail_worker((42, "/tmp/test.cbz", None, None))

    assert payload["error"] is False
    assert payload["warning_details"] == [
        {
            "comic_id": 42,
            "file_path": "/tmp/test.cbz",
            "category": "UserWarning",
            "message": "Corrupt EXIF data. Expecting to read 2 bytes but only got 0.",
        }
    ]


def test_thumbnail_worker_exception_path(monkeypatch):
    class FakeImageService:
        def process_cover(self, file_path, target_path, **_overrides):
            raise RuntimeError("explode")

    monkeypatch.setattr("app.services.images.ImageService", FakeImageService)

    payload = _thumbnail_worker((7, "/tmp/test.cbz", None, None))

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
                "warning_details": item.get("warning_details", []),
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
            {
                "comic_id": 2,
                "error": True,
                "warning_details": [
                    {
                        "comic_id": 2,
                        "file_path": "/tmp/two.cbz",
                        "category": "UserWarning",
                        "message": "Corrupt EXIF data.",
                    }
                ],
            },
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
    assert summary["warnings"] == 1
    assert summary["warning_details"] == [
        {
            "comic_id": 2,
            "file_path": "/tmp/two.cbz",
            "category": "UserWarning",
            "message": "Corrupt EXIF data.",
        }
    ]


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
        cover_override_page=None,
        cover_override_image=None,
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
        cover_override_page=None,
        cover_override_image=None,
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
        cover_override_page=None,
        cover_override_image=None,
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




BLUE = (30, 40, 200)
RED = (200, 30, 40)


def _colored_page(color):
    image = Image.new("RGB", (48, 72), color)
    ImageDraw.Draw(image).rectangle((0, 60, 47, 71), fill=(245, 245, 245))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _write_cbz(path: Path, pages: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in pages.items():
            archive.writestr(name, data)


def _thumbnail_top_color(path) -> tuple:
    with Image.open(path) as thumbnail:
        return thumbnail.convert("RGB").getpixel((thumbnail.width // 2, 2))


def _is_close(actual, expected, tolerance: int = 40) -> bool:
    return all(abs(a - e) <= tolerance for a, e in zip(actual, expected))


def test_process_comic_thumbnail_uses_cover_override_and_clears_dirty(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cover_dir", tmp_path / "cover")
    lib, _series, volume = _seed_library_and_comics(db, tmp_path, lib_name="override-lib")
    _write_cbz(tmp_path / "override-lib" / "issue.cbz", {"01.png": _colored_page(BLUE), "02.png": _colored_page(RED)})
    old_timestamp = datetime(2020, 1, 1, tzinfo=timezone.utc)
    comic = create_comic(
        db, volume, lib.active_root, "issue.cbz",
        filename="issue.cbz",
        is_dirty=True,
        cover_override_page="02.png",
        updated_at=old_timestamp,
    )
    db.commit()

    assert ThumbnailService(db).process_comic_thumbnail(comic) is True

    db.refresh(comic)
    assert Path(comic.thumbnail_path) == tmp_path / "cover" / f"comic_{comic.id}.webp"
    assert _is_close(_thumbnail_top_color(comic.thumbnail_path), RED)
    assert comic.color_primary is not None
    assert comic.is_dirty is False
    assert comic.updated_at.replace(tzinfo=timezone.utc) > old_timestamp


def test_process_comic_thumbnail_marks_comic_dirty_when_generation_fails(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cover_dir", tmp_path / "cover")
    lib, _series, volume = _seed_library_and_comics(db, tmp_path, lib_name="missing-lib")
    comic = create_comic(db, volume, lib.active_root, "missing.cbz", filename="missing.cbz", is_dirty=False)
    db.commit()

    assert ThumbnailService(db).process_comic_thumbnail(comic) is False

    db.refresh(comic)
    assert comic.is_dirty is True
    assert comic.thumbnail_path is None


def test_cover_override_survives_rescan_and_regenerated_thumbnail_uses_it(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cover_dir", tmp_path / "cover")
    root_path = tmp_path / "rescan-lib"
    archive_path = root_path / "Series" / "issue.cbz"
    _write_cbz(archive_path, {"01.png": _colored_page(BLUE), "02.png": _colored_page(RED)})

    lib, series, volume = _seed_library_and_comics(db, tmp_path, lib_name="rescan-lib")
    root = lib.active_root
    comic = create_comic(
        db, volume, root, "Series/issue.cbz",
        filename="issue.cbz",
        number="1",
        cover_override_page="02.png",
    )
    db.commit()

    # The file changes on disk (a new page is added in front), then a rescan updates the same row
    _write_cbz(archive_path, {"00.png": _colored_page(BLUE), "01.png": _colored_page(BLUE), "02.png": _colored_page(RED)})
    no_op = MagicMock(return_value=[])
    stats = metadata_writer_module._apply_metadata_batch(
        db,
        [{
            "file_path": str(archive_path),
            "mtime": 2.0,
            "size": archive_path.stat().st_size,
            "metadata": {"series": series.name, "volume": "1", "number": "1", "page_count": 3},
            "error": False,
        }],
        {(root.id, "Series/issue.cbz"): comic},
        lambda name, *_args: series,
        lambda _series, _volume_num, _file_path: volume,
        SimpleNamespace(
            get_or_create_characters=no_op,
            get_or_create_teams=no_op,
            get_or_create_locations=no_op,
            get_or_create_genres=no_op,
        ),
        SimpleNamespace(add_credits_to_comic=MagicMock()),
        SimpleNamespace(update_comic_reading_lists=MagicMock()),
        SimpleNamespace(update_comic_collections=MagicMock()),
        library_root_id=root.id,
        library_root_path=str(root_path),
    )

    assert stats["updated"] == 1
    rescanned = db.query(Comic).filter_by(library_root_id=root.id, relative_path="Series/issue.cbz").one()
    assert rescanned.id == comic.id
    assert rescanned.cover_override_page == "02.png"
    assert rescanned.is_dirty is True

    # The thumbnail task picks up the dirty comic and honours the override
    dirty_comics = ThumbnailService(db, library_id=lib.id)._get_target_comics()
    assert [c.id for c in dirty_comics] == [comic.id]
    payload = _thumbnail_worker(_thumbnail_task(dirty_comics[0]))
    _apply_batch(db, [payload])

    db.refresh(rescanned)
    assert rescanned.is_dirty is False
    assert _is_close(_thumbnail_top_color(rescanned.thumbnail_path), RED)
