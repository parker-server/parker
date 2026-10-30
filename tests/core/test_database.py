from sqlalchemy import text

from app.database import refresh_query_planner_statistics
from app.models.comic import Volume
from app.models.series import Series
from tests.factories import create_comic, create_library_with_root


def _statistics_rows(db) -> list:
    return db.execute(text("SELECT tbl, idx FROM sqlite_stat1")).all()


def _has_statistics_table(db) -> bool:
    return db.execute(text("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_stat1'")).first() is not None


def _seed_comic(db, number: str):
    volume = db.query(Volume).first()
    if volume is None:
        library = create_library_with_root(db, "Stats Library", "/tmp/stats-library")
        series = Series(name="Stats Series", library_id=library.id)
        db.add(series)
        db.flush()
        volume = Volume(series_id=series.id, volume_number=1)
        db.add(volume)
        db.flush()
    root = volume.series.library.active_root
    create_comic(db, volume, root, f"stats-{number}.cbz", number=number, filename=f"stats-{number}.cbz")
    db.commit()


def test_refresh_creates_statistics_for_indexed_tables(db):
    _seed_comic(db, "1")
    assert not _has_statistics_table(db)

    assert refresh_query_planner_statistics(db) is True

    tables = {row.tbl for row in _statistics_rows(db)}
    assert "comics" in tables


def test_refresh_only_if_missing_skips_when_statistics_exist(db):
    _seed_comic(db, "1")
    assert refresh_query_planner_statistics(db, only_if_missing=True) is True
    before = _statistics_rows(db)

    _seed_comic(db, "2")

    assert refresh_query_planner_statistics(db, only_if_missing=True) is False
    assert _statistics_rows(db) == before


def test_refresh_without_only_if_missing_updates_existing_statistics(db):
    _seed_comic(db, "1")
    refresh_query_planner_statistics(db)
    comics_rows = db.execute(text("SELECT stat FROM sqlite_stat1 WHERE tbl = 'comics' AND idx = 'ix_comics_id'")).scalar()

    _seed_comic(db, "2")
    refresh_query_planner_statistics(db)

    updated = db.execute(text("SELECT stat FROM sqlite_stat1 WHERE tbl = 'comics' AND idx = 'ix_comics_id'")).scalar()
    assert comics_rows.split()[0] == "1"
    assert updated.split()[0] == "2"
