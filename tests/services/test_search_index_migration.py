from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.comic import Comic, Volume
from app.models.series import Series
from tests.factories import create_library_with_root

ALEMBIC_DIR = Path(__file__).resolve().parents[2] / "alembic"
FTS_TRIGGERS = {"comics_fts_ins", "comics_fts_del", "comics_fts_upd"}
REVISION_BEFORE_FTS_FIX = "c5f1a9d3e7b2"


@pytest.fixture
def migrated_db(tmp_path, monkeypatch):
    """A scratch SQLite file that the real migration chain upgrades."""
    db_path = tmp_path / "comics.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path.as_posix()}")

    # No ini file, so env.py skips fileConfig and leaves test logging alone.
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_DIR))

    engine = create_engine(settings.database_url)
    yield config, engine
    engine.dispose()


def _fts_triggers(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'comics_fts%'")
        ).scalars()
        return set(rows)


def _fts_rows(engine) -> dict[int, tuple]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT rowid, title, series, summary FROM comics_fts")).all()
        return {row[0]: tuple(row[1:]) for row in rows}


def _add_comic(session, library, volume, number: str, title: str) -> Comic:
    comic = Comic(
        volume=volume,
        number=number,
        title=title,
        summary=f"{title} summary",
        filename=f"{number}.cbz",
        library_root_id=library.active_root.id,
        relative_path=f"{number}.cbz",
    )
    session.add(comic)
    session.commit()
    return comic


def test_migrating_to_head_backfills_comics_missed_by_dropped_triggers(migrated_db):
    config, engine = migrated_db
    command.upgrade(config, REVISION_BEFORE_FTS_FIX)

    # Earlier batch_alter_table("comics") rebuilds dropped the triggers.
    assert _fts_triggers(engine) == set()

    session = sessionmaker(bind=engine)()
    library = create_library_with_root(session, "fts-backfill-lib", "/tmp/fts-backfill-lib")
    series = Series(name="Backfill Series", library=library)
    volume = Volume(series=series, volume_number=1)
    session.add_all([series, volume])
    comic = _add_comic(session, library, volume, "1", "Missed Comic")
    comic_id = comic.id
    session.close()

    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO comics_fts(rowid, title, series, summary) VALUES (999, 'Deleted', 'Gone', '')")
        )
    assert comic_id not in _fts_rows(engine)

    command.upgrade(config, "head")

    assert _fts_rows(engine) == {
        comic_id: ("Missed Comic", "Backfill Series", "Missed Comic summary"),
    }


def test_fts_triggers_exist_at_head_and_keep_index_in_sync(migrated_db):
    config, engine = migrated_db
    command.upgrade(config, "head")

    assert _fts_triggers(engine) == FTS_TRIGGERS

    session = sessionmaker(bind=engine)()
    library = create_library_with_root(session, "fts-sync-lib", "/tmp/fts-sync-lib")
    series = Series(name="Sync Series", library=library)
    volume = Volume(series=series, volume_number=1)
    session.add_all([series, volume])

    comic = _add_comic(session, library, volume, "1", "Original Title")
    assert _fts_rows(engine) == {comic.id: ("Original Title", "Sync Series", "Original Title summary")}

    comic.title = "Renamed Title"
    session.commit()
    assert _fts_rows(engine)[comic.id] == ("Renamed Title", "Sync Series", "Original Title summary")

    with engine.connect() as conn:
        matched = conn.execute(
            text("SELECT rowid FROM comics_fts WHERE comics_fts MATCH :term"), {"term": '"Renamed" *'}
        ).scalars().all()
    assert matched == [comic.id]

    session.delete(comic)
    session.commit()
    assert _fts_rows(engine) == {}
    session.close()
