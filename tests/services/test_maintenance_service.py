from pathlib import Path
from unittest.mock import MagicMock

from app.models.collection import Collection, CollectionItem
from app.models.comic import Comic, Volume
from app.models.credits import ComicCredit, Person
from app.models.library import Library
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.series import Series
from app.models.tags import Character, Location, Team
from app.services.maintenance import MaintenanceService
import app.services.maintenance as maintenance_module


def _create_library(db, name: str, tmp_path: Path) -> Library:
    lib = Library(name=name, path=str(tmp_path / name))
    db.add(lib)
    db.flush()
    return lib


def _create_comic(db, library: Library, slug: str, file_path: str, *, thumbnail_path: str = None) -> Comic:
    series = Series(name=f"{slug}-series", library_id=library.id)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="1",
        title=f"{slug}-title",
        filename=f"{slug}.cbz",
        file_path=file_path,
        page_count=12,
        thumbnail_path=thumbnail_path,
    )
    db.add_all([series, volume, comic])
    db.flush()
    return comic


def test_cleanup_orphans_global_removes_only_orphans(db, tmp_path):
    lib = _create_library(db, "maint-global", tmp_path)

    # Keep-path entities
    keep_series = Series(name="keep-series", library_id=lib.id)
    keep_volume = Volume(series=keep_series, volume_number=1)
    keep_comic = Comic(
        volume=keep_volume,
        number="1",
        title="keep",
        filename="keep.cbz",
        file_path=str(tmp_path / "keep.cbz"),
        page_count=10,
    )

    # Orphan series/volumes
    empty_series = Series(name="empty-series", library_id=lib.id)
    empty_volume = Volume(series=empty_series, volume_number=1)
    no_volume_series = Series(name="no-volume-series", library_id=lib.id)

    char_keep = Character(name="char-keep")
    char_orphan = Character(name="char-orphan")
    keep_comic.characters.append(char_keep)

    team_keep = Team(name="team-keep")
    team_orphan = Team(name="team-orphan")
    keep_comic.teams.append(team_keep)

    loc_keep = Location(name="loc-keep")
    loc_orphan = Location(name="loc-orphan")
    keep_comic.locations.append(loc_keep)

    person_keep = Person(name="person-keep")
    person_orphan = Person(name="person-orphan")

    rl_auto_empty = ReadingList(name="rl-auto-empty", auto_generated=1)
    rl_manual_empty = ReadingList(name="rl-manual-empty", auto_generated=0)
    rl_auto_filled = ReadingList(name="rl-auto-filled", auto_generated=1)

    col_auto_empty = Collection(name="col-auto-empty", auto_generated=1)
    col_manual_empty = Collection(name="col-manual-empty", auto_generated=0)
    col_auto_filled = Collection(name="col-auto-filled", auto_generated=1)

    db.add_all([
        keep_series,
        keep_volume,
        keep_comic,
        empty_series,
        empty_volume,
        no_volume_series,
        char_keep,
        char_orphan,
        team_keep,
        team_orphan,
        loc_keep,
        loc_orphan,
        person_keep,
        person_orphan,
        rl_auto_empty,
        rl_manual_empty,
        rl_auto_filled,
        col_auto_empty,
        col_manual_empty,
        col_auto_filled,
    ])
    db.flush()

    db.add_all([
        ComicCredit(comic_id=keep_comic.id, person_id=person_keep.id, role="writer"),
        ReadingListItem(reading_list_id=rl_auto_filled.id, comic_id=keep_comic.id, position=1),
        CollectionItem(collection_id=col_auto_filled.id, comic_id=keep_comic.id),
    ])
    db.commit()

    service = MaintenanceService(db)
    stats = service.cleanup_orphans()

    assert stats == {
        "series": 2,
        "volumes": 1,
        "characters": 1,
        "teams": 1,
        "locations": 1,
        "people": 1,
        "empty_lists": 1,
        "empty_collections": 1,
    }

    assert db.query(Series).filter(Series.name == "keep-series").count() == 1
    assert db.query(Series).filter(Series.name == "empty-series").count() == 0
    assert db.query(Series).filter(Series.name == "no-volume-series").count() == 0

    assert db.query(Character).filter(Character.name == "char-keep").count() == 1
    assert db.query(Character).filter(Character.name == "char-orphan").count() == 0
    assert db.query(Team).filter(Team.name == "team-orphan").count() == 0
    assert db.query(Location).filter(Location.name == "loc-orphan").count() == 0
    assert db.query(Person).filter(Person.name == "person-orphan").count() == 0

    assert db.query(ReadingList).filter(ReadingList.name == "rl-auto-empty").count() == 0
    assert db.query(ReadingList).filter(ReadingList.name == "rl-manual-empty").count() == 1
    assert db.query(ReadingList).filter(ReadingList.name == "rl-auto-filled").count() == 1

    assert db.query(Collection).filter(Collection.name == "col-auto-empty").count() == 0
    assert db.query(Collection).filter(Collection.name == "col-manual-empty").count() == 1
    assert db.query(Collection).filter(Collection.name == "col-auto-filled").count() == 1


def test_cleanup_orphans_scoped_only_touches_target_library(db, tmp_path):
    lib_a = _create_library(db, "maint-scope-a", tmp_path)
    lib_b = _create_library(db, "maint-scope-b", tmp_path)

    # Library A orphans
    series_a_empty = Series(name="a-empty", library_id=lib_a.id)
    vol_a_empty = Volume(series=series_a_empty, volume_number=1)
    series_a_no_vol = Series(name="a-no-vol", library_id=lib_a.id)

    # Library B should remain untouched
    series_b_empty = Series(name="b-empty", library_id=lib_b.id)
    vol_b_empty = Volume(series=series_b_empty, volume_number=1)
    series_b_no_vol = Series(name="b-no-vol", library_id=lib_b.id)

    # Global deep cleanup entities should be skipped in scoped mode
    char_orphan = Character(name="scoped-char-orphan")

    db.add_all([
        series_a_empty,
        vol_a_empty,
        series_a_no_vol,
        series_b_empty,
        vol_b_empty,
        series_b_no_vol,
        char_orphan,
    ])
    db.commit()

    service = MaintenanceService(db)
    service.logger = MagicMock()

    stats = service.cleanup_orphans(library_id=lib_a.id)

    assert stats["volumes"] == 1
    assert stats["series"] == 2
    assert stats["characters"] == 0
    assert stats["teams"] == 0
    assert stats["locations"] == 0
    assert stats["people"] == 0
    assert stats["empty_lists"] == 0
    assert stats["empty_collections"] == 0

    assert db.query(Series).filter(Series.name == "a-empty").count() == 0
    assert db.query(Series).filter(Series.name == "a-no-vol").count() == 0
    assert db.query(Series).filter(Series.name == "b-empty").count() == 1
    assert db.query(Series).filter(Series.name == "b-no-vol").count() == 1
    assert db.query(Character).filter(Character.name == "scoped-char-orphan").count() == 1

    service.logger.info.assert_called_with(f"Skipping deep tag cleanup for scoped scan (Library {lib_a.id})")


def test_cleanup_missing_files_scoped_with_batch_commits(db, tmp_path, monkeypatch):
    lib_a = _create_library(db, "maint-missing-a", tmp_path)
    lib_b = _create_library(db, "maint-missing-b", tmp_path)

    # 101 missing files in lib_a to trigger both the modulo-100 commit and final commit
    missing_ids = []
    for i in range(101):
        comic = _create_comic(db, lib_a, f"a-missing-{i}", str(tmp_path / "missing" / f"a-{i}.cbz"))
        missing_ids.append(comic.id)

    existing_path = tmp_path / "exists" / "a-exists.cbz"
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_path.write_bytes(b"ok")
    keep_comic = _create_comic(db, lib_a, "a-keep", str(existing_path))

    other_lib_missing = _create_comic(db, lib_b, "b-missing", str(tmp_path / "missing" / "b.cbz"))
    db.commit()

    service = MaintenanceService(db)

    original_commit = db.commit
    commit_spy = MagicMock(side_effect=original_commit)
    monkeypatch.setattr(db, "commit", commit_spy)

    deleted_ids = service.cleanup_missing_files(library_id=lib_a.id)

    assert len(deleted_ids) == 101
    assert set(deleted_ids) == set(missing_ids)
    assert commit_spy.call_count == 2

    assert db.get(Comic, keep_comic.id) is not None
    assert db.get(Comic, other_lib_missing.id) is not None


def test_cleanup_missing_files_no_deletions_makes_no_commit(db, tmp_path, monkeypatch):
    lib = _create_library(db, "maint-missing-none", tmp_path)

    existing_path = tmp_path / "exists" / "one.cbz"
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_path.write_bytes(b"ok")
    _create_comic(db, lib, "keep", str(existing_path))
    db.commit()

    service = MaintenanceService(db)

    original_commit = db.commit
    commit_spy = MagicMock(side_effect=original_commit)
    monkeypatch.setattr(db, "commit", commit_spy)

    assert service.cleanup_missing_files() == []
    assert commit_spy.call_count == 0


def test_delete_thumbnails_by_id_handles_success_missing_and_error(db, tmp_path, monkeypatch):
    service = MaintenanceService(db)
    service.logger = MagicMock()

    cover_dir = tmp_path / "cover"
    cover_dir.mkdir(parents=True)
    monkeypatch.setattr(maintenance_module.settings, "cover_dir", cover_dir)

    ok_file = cover_dir / "cover_1.webp"
    err_file = cover_dir / "cover_2.webp"
    ok_file.write_bytes(b"ok")
    err_file.write_bytes(b"err")

    original_unlink = Path.unlink

    def selective_unlink(path_obj, *args, **kwargs):
        if path_obj.name == "cover_2.webp":
            raise OSError("unlink failed")
        return original_unlink(path_obj, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", selective_unlink)

    service.delete_thumbnails_by_id([1, 2, 3])

    assert not ok_file.exists()
    assert err_file.exists()
    service.logger.error.assert_called_once()


def test_cleanup_orphaned_thumbnails_handles_missing_root(db, tmp_path, monkeypatch):
    service = MaintenanceService(db)
    missing_cover_dir = tmp_path / "no-cover-dir"
    monkeypatch.setattr(maintenance_module.settings, "cover_dir", missing_cover_dir)

    assert service.cleanup_orphaned_thumbnails() == 0


def test_cleanup_orphaned_thumbnails_deletes_unreferenced_and_logs_errors(db, tmp_path, monkeypatch):
    service = MaintenanceService(db)
    service.logger = MagicMock()

    cover_dir = tmp_path / "cover"
    cover_dir.mkdir(parents=True)
    monkeypatch.setattr(maintenance_module.settings, "cover_dir", cover_dir)

    referenced = cover_dir / "referenced.webp"
    orphan = cover_dir / "orphan.webp"
    error_file = cover_dir / "error.webp"
    referenced.write_bytes(b"ref")
    orphan.write_bytes(b"orph")
    error_file.write_bytes(b"err")
    (cover_dir / "nested").mkdir()

    lib = _create_library(db, "maint-thumb-lib", tmp_path)
    _create_comic(db, lib, "thumb-comic", str(tmp_path / "thumb.cbz"), thumbnail_path=str(referenced))
    db.commit()

    original_unlink = Path.unlink

    def selective_unlink(path_obj, *args, **kwargs):
        if path_obj.name == "error.webp":
            raise OSError("cannot delete")
        return original_unlink(path_obj, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", selective_unlink)

    deleted = service.cleanup_orphaned_thumbnails()

    assert deleted == 1
    assert referenced.exists()
    assert not orphan.exists()
    assert error_file.exists()
    service.logger.error.assert_called_once()


def test_refresh_reading_list_descriptions_batches_and_commits(db, monkeypatch):
    lists = [ReadingList(name=f"auto-{i}", auto_generated=1, description=None) for i in range(52)]
    db.add_all(lists)
    db.commit()

    service = MaintenanceService(db)
    service.enrichment = MagicMock()
    service.enrichment.get_description.side_effect = lambda name: f"desc-{name}"

    original_commit = db.commit
    commit_spy = MagicMock(side_effect=original_commit)
    monkeypatch.setattr(db, "commit", commit_spy)

    result = service.refresh_reading_list_descriptions()

    assert result == {"updated": 52, "total_scanned": 52}
    assert commit_spy.call_count == 2


def test_refresh_reading_list_descriptions_no_updates(db, monkeypatch):
    db.add_all([
        ReadingList(name="same-a", auto_generated=1, description="desc-same-a"),
        ReadingList(name="same-b", auto_generated=1, description="desc-same-b"),
        ReadingList(name="none-c", auto_generated=1, description="existing"),
    ])
    db.commit()

    service = MaintenanceService(db)

    def description_lookup(name):
        if name == "none-c":
            return None
        return f"desc-{name}"

    service.enrichment = MagicMock()
    service.enrichment.get_description.side_effect = description_lookup

    original_commit = db.commit
    commit_spy = MagicMock(side_effect=original_commit)
    monkeypatch.setattr(db, "commit", commit_spy)

    result = service.refresh_reading_list_descriptions()

    assert result == {"updated": 0, "total_scanned": 3}
    assert commit_spy.call_count == 0
