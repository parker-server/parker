import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.series import Series
import app.database as database_module
import app.services.workers.metadata_writer as metadata_writer_module


class _ReadQueue:
    def __init__(self, items):
        self.items = list(items)

    def get(self):
        if not self.items:
            raise AssertionError("Queue unexpectedly empty")
        return self.items.pop(0)


class _WriteQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


class _FakeQuery:
    def __init__(self, model, library_obj):
        self.model = model
        self.library_obj = library_obj

    def get(self, _library_id):
        return self.library_obj

    def join(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def filter_by(self, **_kwargs):
        return self

    def first(self):
        return None

    def all(self):
        return []


class _FakeDB:
    def __init__(self, library_path):
        self.library_obj = SimpleNamespace(path=library_path)
        self.closed = False

    def query(self, model):
        return _FakeQuery(model, self.library_obj)

    def close(self):
        self.closed = True


def _metadata(**overrides):
    payload = {
        "series": "Series A",
        "volume": "1",
        "number": "1/2",
        "page_count": 24,
        "title": "Issue Title",
        "summary": "Summary",
        "year": "2024",
        "month": "6",
        "day": "15",
        "web": "https://example.com",
        "notes": "note",
        "age_rating": "Teen",
        "lang": "en",
        "community_rating": 4.5,
        "publisher": "Marvel",
        "imprint": "Max",
        "format": "One-Shot",
        "series_group": "Event",
        "scan_information": "scanner",
        "alternate_series": "Alt",
        "alternate_number": "A1",
        "story_arc": "Arc",
        "count": "12",
        "raw_metadata": {"a": 1},
        "characters": ["Batman"],
        "teams": ["JL"],
        "locations": ["Gotham"],
        "genre": ["Superhero"],
    }
    payload.update(overrides)
    return payload


def test_apply_metadata_batch_import_update_and_error_paths(db):
    library = Library(name="writer-lib", path="/tmp/writer-lib")
    db.add(library)
    db.flush()

    existing_series = Series(name="Series A", library_id=library.id)
    existing_volume = Volume(series=existing_series, volume_number=1)
    db.add_all([existing_series, existing_volume])
    db.flush()

    existing_comic_path = "/tmp/existing.cbz"
    existing_comic = Comic(
        volume_id=existing_volume.id,
        filename="existing.cbz",
        file_path=existing_comic_path,
        page_count=10,
        number="1",
    )
    db.add(existing_comic)
    db.commit()

    existing_map = {existing_comic_path: existing_comic}

    def get_or_create_series(name: str):
        series = db.query(Series).filter_by(name=name, library_id=library.id).first()
        if not series:
            series = Series(name=name, library_id=library.id)
            db.add(series)
            db.flush()
        return series

    def get_or_create_volume(series, volume_num: int, _file_path: str):
        volume = db.query(Volume).filter_by(series_id=series.id, volume_number=volume_num).first()
        if not volume:
            volume = Volume(series_id=series.id, volume_number=volume_num)
            db.add(volume)
            db.flush()
        return volume

    tag_service = SimpleNamespace(
        get_or_create_characters=MagicMock(return_value=[]),
        get_or_create_teams=MagicMock(return_value=[]),
        get_or_create_locations=MagicMock(return_value=[]),
        get_or_create_genres=MagicMock(return_value=[]),
    )
    credit_service = SimpleNamespace(add_credits_to_comic=MagicMock())
    reading_list_service = SimpleNamespace(update_comic_reading_lists=MagicMock())
    collection_service = SimpleNamespace(update_comic_collections=MagicMock())

    batch = [
        {"file_path": "/tmp/bad.cbz", "error": True, "message": "bad archive"},
        {
            "file_path": existing_comic_path,
            "mtime": 111.0,
            "size": 222,
            "metadata": _metadata(number="1/2", title="Updated Existing", count="3"),
            "error": False,
        },
        {
            "file_path": "/tmp/new.cbz",
            "mtime": 333.0,
            "size": 444,
            "metadata": _metadata(series=None, volume=None, number="2", title="Imported New", raw_metadata={"n": 1}),
            "error": False,
        },
    ]

    stats = metadata_writer_module._apply_metadata_batch(
        db,
        batch,
        existing_map,
        get_or_create_series,
        get_or_create_volume,
        tag_service,
        credit_service,
        reading_list_service,
        collection_service,
    )

    db.refresh(existing_comic)
    imported_comic = db.query(Comic).filter_by(file_path="/tmp/new.cbz").first()
    unknown_series = db.query(Series).filter_by(name="Unknown Series", library_id=library.id).first()

    assert stats["imported"] == 1
    assert stats["updated"] == 1
    assert stats["errors"] == 1
    assert stats["skipped"] == 0
    assert stats["error_details"] == [{"file_path": "/tmp/bad.cbz", "message": "bad archive"}]

    assert existing_comic.number == "0.5"
    assert existing_comic.title == "Updated Existing"
    assert existing_comic.count == 3
    assert existing_comic.is_dirty is True

    assert imported_comic is not None
    assert imported_comic.title == "Imported New"
    assert imported_comic.number == "2"
    assert imported_comic.is_dirty is True
    assert json.loads(imported_comic.metadata_json) == {"n": 1}
    assert unknown_series is not None

    assert existing_map["/tmp/new.cbz"].id == imported_comic.id
    assert credit_service.add_credits_to_comic.call_count == 2
    assert reading_list_service.update_comic_reading_lists.call_count == 2
    assert collection_service.update_comic_collections.call_count == 2


def test_metadata_writer_batches_and_emits_summary(monkeypatch, tmp_path):
    fake_db = _FakeDB(str(tmp_path / "lib"))
    applied_batches = []

    def fake_apply_batch(db, batch, *_args, **_kwargs):
        applied_batches.append(list(batch))
        if len(batch) == 2:
            return {
                "imported": 1,
                "updated": 0,
                "errors": 1,
                "skipped": 0,
                "error_details": [{"file_path": "a.cbz", "message": "bad"}],
            }
        return {
            "imported": 0,
            "updated": 1,
            "errors": 0,
            "skipped": 0,
            "error_details": [],
        }

    monkeypatch.setattr(database_module.engine, "dispose", MagicMock())
    monkeypatch.setattr(database_module, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(metadata_writer_module, "_apply_metadata_batch", fake_apply_batch)

    result_queue = _ReadQueue([
        {"file_path": "a.cbz", "error": False, "mtime": 1.0, "size": 1, "metadata": {"page_count": 1}},
        {"file_path": "b.cbz", "error": False, "mtime": 1.0, "size": 1, "metadata": {"page_count": 1}},
        {"file_path": "c.cbz", "error": False, "mtime": 1.0, "size": 1, "metadata": {"page_count": 1}},
        None,
    ])
    stats_queue = _WriteQueue()

    metadata_writer_module.metadata_writer(result_queue, stats_queue, library_id=5, batch_size=2)

    assert len(applied_batches) == 2
    assert [len(batch) for batch in applied_batches] == [2, 1]

    summary = stats_queue.items[-1]
    assert summary["summary"] is True
    assert summary["imported"] == 1
    assert summary["updated"] == 1
    assert summary["errors"] == 1
    assert summary["skipped"] == 0
    assert summary["error_details"] == [{"file_path": "a.cbz", "message": "bad"}]
    assert fake_db.closed is True


def test_metadata_writer_emits_error_summary_on_exception(monkeypatch):
    monkeypatch.setattr(database_module, "SessionLocal", MagicMock(side_effect=RuntimeError("db boom")))

    result_queue = _ReadQueue([])
    stats_queue = _WriteQueue()

    metadata_writer_module.metadata_writer(result_queue, stats_queue, library_id=9, batch_size=2)

    summary = stats_queue.items[-1]
    assert summary["summary"] is True
    assert summary["imported"] == 0
    assert summary["updated"] == 0
    assert summary["errors"] == 1
    assert summary["skipped"] == 0
    assert summary["error_details"][0]["file_path"] is None
    assert "db boom" in summary["error_details"][0]["message"]



def test_metadata_writer_creates_series_volume_once_and_reuses_cache(monkeypatch, db, tmp_path):
    library_path = tmp_path / "writer-cache-lib"
    library_path.mkdir(parents=True, exist_ok=True)

    library = Library(name="writer-cache-lib", path=str(library_path))
    db.add(library)
    db.commit()
    db.refresh(library)
    library_id = library.id

    class DummyTagService:
        def __init__(self, _db):
            pass

        def get_or_create_characters(self, _vals):
            return []

        def get_or_create_teams(self, _vals):
            return []

        def get_or_create_locations(self, _vals):
            return []

        def get_or_create_genres(self, _vals):
            return []

    class DummyCreditService:
        def __init__(self, _db):
            pass

        def add_credits_to_comic(self, comic, _metadata):
            return comic

    class DummyReadingListService:
        def __init__(self, _db):
            pass

        def update_comic_reading_lists(self, comic, _alt_series, _alt_number):
            return comic

    class DummyCollectionService:
        def __init__(self, _db):
            pass

        def update_comic_collections(self, comic, _series_group):
            return comic

    sidecar_lookup = MagicMock(side_effect=lambda path, entity: f"{entity}:{Path(path).name}")

    monkeypatch.setattr(database_module.engine, "dispose", MagicMock())
    monkeypatch.setattr(database_module, "SessionLocal", lambda: db)
    monkeypatch.setattr("app.services.tags.TagService", DummyTagService)
    monkeypatch.setattr("app.services.credits.CreditService", DummyCreditService)
    monkeypatch.setattr("app.services.reading_list.ReadingListService", DummyReadingListService)
    monkeypatch.setattr("app.services.collection.CollectionService", DummyCollectionService)
    monkeypatch.setattr("app.services.sidecar_service.SidecarService.get_summary_from_disk", sidecar_lookup)

    item_one_path = str(library_path / "Brand Series" / "Vol 2" / "one.cbz")
    item_two_path = str(library_path / "Brand Series" / "Vol 2" / "two.cbz")

    result_queue = _ReadQueue([
        {
            "file_path": item_one_path,
            "mtime": 1.0,
            "size": 100,
            "metadata": _metadata(series="Brand Series", volume="2", number=None, raw_metadata={"one": 1}),
            "error": False,
        },
        {
            "file_path": item_two_path,
            "mtime": 2.0,
            "size": 200,
            "metadata": _metadata(series="Brand Series", volume="2", number="5", raw_metadata={"two": 2}),
            "error": False,
        },
        None,
    ])
    stats_queue = _WriteQueue()

    metadata_writer_module.metadata_writer(result_queue, stats_queue, library_id=library_id, batch_size=50)

    summary = stats_queue.items[-1]
    assert summary["summary"] is True
    assert summary["imported"] == 2
    assert summary["updated"] == 0
    assert summary["errors"] == 0

    created_series = db.query(Series).filter_by(name="Brand Series", library_id=library_id).all()
    created_volumes = db.query(Volume).join(Series).filter(Series.library_id == library_id).all()
    created_comics = db.query(Comic).join(Volume).join(Series).filter(Series.library_id == library_id).all()

    assert len(created_series) == 1
    assert len(created_volumes) == 1
    assert len(created_comics) == 2

    first_comic = db.query(Comic).filter_by(file_path=item_one_path).first()
    second_comic = db.query(Comic).filter_by(file_path=item_two_path).first()
    assert first_comic.number is None
    assert second_comic.number == "5"

    assert created_series[0].summary_override == "series:Brand Series"
    assert created_volumes[0].summary_override == "volume:Vol 2"
    assert sidecar_lookup.call_count == 2


