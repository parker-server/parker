import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.comic import Comic, Volume
from app.models.series import Series
import app.database as database_module
import app.services.workers.metadata_writer as metadata_writer_module
from tests.factories import create_comic, create_library_with_root


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
    def __init__(self, model, library_obj, library_root_obj):
        self.model = model
        self.library_obj = library_obj
        self.library_root_obj = library_root_obj

    def get(self, _library_id):
        return self.library_obj

    def join(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def filter_by(self, **_kwargs):
        return self

    def first(self):
        return self.library_root_obj

    def all(self):
        return []


class _FakeDB:
    def __init__(self, library_path):
        self.library_obj = SimpleNamespace(name="fake-lib")
        self.library_root_obj = SimpleNamespace(id=1, path=library_path)
        self.closed = False

    def get(self, _model, _library_id):
        return self.library_obj

    def query(self, model):
        return _FakeQuery(model, self.library_obj, self.library_root_obj)

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


def _seed_library_with_root(db, name, path):
    library = create_library_with_root(db, name, path)
    return library, library.active_root


def test_apply_metadata_batch_import_update_and_error_paths(db):
    library, root = _seed_library_with_root(db, "writer-lib", "/tmp/writer-lib")

    existing_series = Series(name="Series A", library_id=library.id)
    existing_volume = Volume(series=existing_series, volume_number=1)
    db.add_all([existing_series, existing_volume])
    db.flush()

    existing_comic_path = "/tmp/writer-lib/existing.cbz"
    existing_comic = create_comic(
        db, existing_volume, root, "existing.cbz",
        filename="existing.cbz",
        page_count=10,
        number="1",
    )
    db.commit()

    existing_by_key = {(root.id, "existing.cbz"): existing_comic}

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
            "file_path": "/tmp/writer-lib/new.cbz",
            "mtime": 333.0,
            "size": 444,
            "metadata": _metadata(series=None, volume=None, number="2", title="Imported New", raw_metadata={"n": 1}),
            "error": False,
        },
    ]

    stats = metadata_writer_module._apply_metadata_batch(
        db,
        batch,
        existing_by_key,
        get_or_create_series,
        get_or_create_volume,
        tag_service,
        credit_service,
        reading_list_service,
        collection_service,
        library_root_id=root.id,
        library_root_path="/tmp/writer-lib",
    )

    db.refresh(existing_comic)
    imported_comic = db.query(Comic).filter_by(relative_path="new.cbz").first()
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

    assert existing_by_key[(root.id, "new.cbz")].id == imported_comic.id
    assert credit_service.add_credits_to_comic.call_count == 2
    assert reading_list_service.update_comic_reading_lists.call_count == 2
    assert collection_service.update_comic_collections.call_count == 2


def test_apply_metadata_batch_matches_existing_comic_by_identity_when_path_differs(db):
    library, root = _seed_library_with_root(db, "writer-identity-lib", "/tmp/writer-identity-lib")

    series = Series(name="Series A", library_id=library.id)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()

    # The comic is already stamped with the identity this scan's freshly computed
    # relative_path resolves to -- that must be recognized as an update, not a
    # duplicate import, even if some other lookup by path would've missed it.
    existing_comic = create_comic(
        db, volume, root, "existing.cbz",
        filename="existing.cbz",
        page_count=10,
        number="1",
    )
    db.commit()

    existing_by_key = {(root.id, "existing.cbz"): existing_comic}

    def get_or_create_series(name: str):
        return db.query(Series).filter_by(name=name, library_id=library.id).first()

    def get_or_create_volume(series_obj, volume_num: int, _file_path: str):
        return db.query(Volume).filter_by(series_id=series_obj.id, volume_number=volume_num).first()

    tag_service = SimpleNamespace(
        get_or_create_characters=MagicMock(return_value=[]),
        get_or_create_teams=MagicMock(return_value=[]),
        get_or_create_locations=MagicMock(return_value=[]),
        get_or_create_genres=MagicMock(return_value=[]),
    )
    credit_service = SimpleNamespace(add_credits_to_comic=MagicMock())
    reading_list_service = SimpleNamespace(update_comic_reading_lists=MagicMock())
    collection_service = SimpleNamespace(update_comic_collections=MagicMock())

    batch = [{
        "file_path": "/tmp/writer-identity-lib/existing.cbz",
        "mtime": 111.0,
        "size": 222,
        "metadata": _metadata(title="Renamed On Disk"),
        "error": False,
    }]

    stats = metadata_writer_module._apply_metadata_batch(
        db,
        batch,
        existing_by_key,
        get_or_create_series,
        get_or_create_volume,
        tag_service,
        credit_service,
        reading_list_service,
        collection_service,
        library_root_id=root.id,
        library_root_path="/tmp/writer-identity-lib",
    )

    assert stats["imported"] == 0
    assert stats["updated"] == 1

    db.refresh(existing_comic)
    assert existing_comic.title == "Renamed On Disk"
    assert db.query(Comic).count() == 1


def test_apply_metadata_batch_disables_optional_metadata_flows(db):
    library, root = _seed_library_with_root(db, "writer-disable-lib", "/tmp/writer-disable-lib")

    series = Series(name="Series A", library_id=library.id)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()

    existing_path = "/tmp/writer-disable-lib/disable-existing.cbz"
    comic = create_comic(
        db, volume, root, "disable-existing.cbz",
        filename="disable-existing.cbz",
        page_count=10,
        number="1",
        alternate_series="Old List",
        alternate_number="1",
        series_group="Old Group",
        story_arc="Old Arc",
    )
    db.commit()

    existing_by_key = {(root.id, "disable-existing.cbz"): comic}

    def get_or_create_series(name: str):
        return db.query(Series).filter_by(name=name, library_id=library.id).first()

    def get_or_create_volume(series_obj, volume_num: int, _file_path: str):
        return db.query(Volume).filter_by(series_id=series_obj.id, volume_number=volume_num).first()

    tag_service = SimpleNamespace(
        get_or_create_characters=MagicMock(return_value=[]),
        get_or_create_teams=MagicMock(return_value=[]),
        get_or_create_locations=MagicMock(return_value=[]),
        get_or_create_genres=MagicMock(return_value=[]),
    )
    credit_service = SimpleNamespace(add_credits_to_comic=MagicMock())
    reading_list_service = SimpleNamespace(
        update_comic_reading_lists=MagicMock(),
    )
    collection_service = SimpleNamespace(
        update_comic_collections=MagicMock(),
    )

    batch = [{
        "file_path": existing_path,
        "mtime": 111.0,
        "size": 222,
        "metadata": _metadata(
            series_group="Event",
            alternate_series="Alt",
            alternate_number="1",
            story_arc="Arc",
        ),
        "error": False,
    }]

    metadata_writer_module._apply_metadata_batch(
        db,
        batch,
        existing_by_key,
        get_or_create_series,
        get_or_create_volume,
        tag_service,
        credit_service,
        reading_list_service,
        collection_service,
        library_root_id=root.id,
        library_root_path="/tmp/writer-disable-lib",
        parse_reading_lists=False,
        parse_collections=False,
        parse_story_arcs=False,
    )

    db.refresh(comic)
    assert comic.alternate_series == "Old List"
    assert comic.alternate_number == "1"
    assert comic.series_group == "Old Group"
    assert comic.story_arc == "Old Arc"
    reading_list_service.update_comic_reading_lists.assert_not_called()
    collection_service.update_comic_collections.assert_not_called()


def test_apply_metadata_batch_falls_back_to_volume_one_for_invalid_volume_metadata(db):
    library, root = _seed_library_with_root(db, "writer-invalid-volume-lib", "/tmp/writer-invalid-volume-lib")

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

    batch = [{
        "file_path": "/tmp/writer-invalid-volume-lib/invalid-volume.cbz",
        "mtime": 123.0,
        "size": 456,
        "metadata": _metadata(volume="3bbbb", title="Invalid Volume"),
        "error": False,
    }]

    stats = metadata_writer_module._apply_metadata_batch(
        db,
        batch,
        {},
        get_or_create_series,
        get_or_create_volume,
        tag_service,
        credit_service,
        reading_list_service,
        collection_service,
        library_root_id=root.id,
        library_root_path="/tmp/writer-invalid-volume-lib",
    )

    imported = db.query(Comic).filter_by(relative_path="invalid-volume.cbz").first()
    assert stats["imported"] == 1
    assert stats["errors"] == 0
    assert imported is not None
    assert imported.volume.volume_number == 1


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

    library, root = _seed_library_with_root(db, "writer-cache-lib", str(library_path))
    db.commit()
    library_id = library.id
    root_id = root.id

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

    first_comic = db.query(Comic).filter_by(library_root_id=root_id, relative_path="Brand Series/Vol 2/one.cbz").first()
    second_comic = db.query(Comic).filter_by(library_root_id=root_id, relative_path="Brand Series/Vol 2/two.cbz").first()
    assert first_comic.number is None
    assert second_comic.number == "5"

    assert created_series[0].summary_override == "series:Brand Series"
    assert created_volumes[0].summary_override == "volume:Vol 2"
    assert sidecar_lookup.call_count == 2


def test_metadata_writer_populates_library_root_and_relative_path(monkeypatch, db, tmp_path):
    library_path = tmp_path / "root-lib"
    library_path.mkdir(parents=True, exist_ok=True)

    library, root = _seed_library_with_root(db, "root-lib", str(library_path))
    db.commit()
    library_id = library.id
    root_id = root.id

    series = Series(name="Unmatched Series", library_id=library.id)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()

    existing_relative_path = "Existing/existing.cbz"
    existing_path = str(library_path / existing_relative_path)
    existing_comic = create_comic(
        db, volume, root, existing_relative_path,
        filename="existing.cbz",
        page_count=1,
        number="1",
    )
    db.commit()

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

    monkeypatch.setattr(database_module.engine, "dispose", MagicMock())
    monkeypatch.setattr(database_module, "SessionLocal", lambda: db)
    monkeypatch.setattr("app.services.tags.TagService", DummyTagService)
    monkeypatch.setattr("app.services.credits.CreditService", DummyCreditService)
    monkeypatch.setattr("app.services.reading_list.ReadingListService", DummyReadingListService)
    monkeypatch.setattr("app.services.collection.CollectionService", DummyCollectionService)

    new_path = str(library_path / "Fresh" / "fresh.cbz")

    result_queue = _ReadQueue([
        {
            "file_path": existing_path,
            "mtime": 1.0,
            "size": 100,
            "metadata": _metadata(series="Unmatched Series", volume="1", number="1"),
            "error": False,
        },
        {
            "file_path": new_path,
            "mtime": 2.0,
            "size": 200,
            "metadata": _metadata(series="Fresh Series", volume="1", number="1"),
            "error": False,
        },
        None,
    ])
    stats_queue = _WriteQueue()

    metadata_writer_module.metadata_writer(result_queue, stats_queue, library_id=library_id, batch_size=50)

    summary = stats_queue.items[-1]
    assert summary["summary"] is True
    assert summary["imported"] == 1
    assert summary["updated"] == 1
    assert summary["errors"] == 0

    refreshed_existing = db.query(Comic).filter_by(library_root_id=root_id, relative_path=existing_relative_path).first()
    new_comic = db.query(Comic).filter_by(library_root_id=root_id, relative_path="Fresh/fresh.cbz").first()

    assert refreshed_existing.library_root_id == root_id
    assert refreshed_existing.relative_path == "Existing/existing.cbz"

    assert new_comic is not None
    assert new_comic.library_root_id == root_id
    assert new_comic.relative_path == "Fresh/fresh.cbz"
