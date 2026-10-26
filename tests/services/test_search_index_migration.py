import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.comic import Comic, Volume
from app.models.series import Series
from tests.factories import create_library_with_root

ALEMBIC_DIR = Path(__file__).resolve().parents[2] / "alembic"
CONTENTLESS_FTS_MIGRATION = ALEMBIC_DIR / "versions" / "f2c7a9e4b1d6_make_comics_fts_contentless.py"
FTS_TRIGGERS = {"comics_fts_ins", "comics_fts_del", "comics_fts_upd"}
REVISION_BEFORE_TRIGGER_FIX = "c5f1a9d3e7b2"
REVISION_TRIGGER_FIX = "e8b4d2a6f1c9"


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


def _load_contentless_fts_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("contentless_fts_migration", CONTENTLESS_FTS_MIGRATION)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _fts_triggers(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'comics_fts%'")
        ).scalars()
        return set(rows)


def _stores_fts_content(engine) -> bool:
    # FTS5 keeps its copy of the text in this shadow table; contentless tables don't have it.
    with engine.connect() as conn:
        found = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'comics_fts_content'")
        ).scalar()
        return found is not None


def _fts_rows(engine) -> dict[int, tuple]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT rowid, title, series, summary FROM comics_fts")).all()
        return {row[0]: tuple(row[1:]) for row in rows}


def _match(engine, word: str) -> list[int]:
    # Same query and term format as SearchService._build_fts_condition.
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT rowid FROM comics_fts WHERE comics_fts MATCH :term"), {"term": f'"{word}" *'}
        ).scalars().all()


def _seed_volume(session, name: str, series_name: str):
    library = create_library_with_root(session, name, f"/tmp/{name}")
    series = Series(name=series_name, library=library)
    volume = Volume(series=series, volume_number=1)
    session.add_all([series, volume])
    session.commit()
    return library, volume


def _add_comic(session, library, volume, number: str, title: str, summary: str) -> Comic:
    comic = Comic(
        volume=volume,
        number=number,
        title=title,
        summary=summary,
        filename=f"{number}.cbz",
        library_root_id=library.active_root.id,
        relative_path=f"{number}.cbz",
    )
    session.add(comic)
    session.commit()
    return comic


def _assert_index_tracks_comic_changes(engine, session, library, volume):
    comic = _add_comic(session, library, volume, "7", "Falcon Rising", "Harbor storm")
    comic_id = comic.id
    assert _match(engine, "Falcon") == [comic_id]
    assert _match(engine, "Harbor") == [comic_id]
    # Other comics in the volume share the series name, so only check this one is included.
    assert comic_id in _match(engine, "Zephyr")

    comic.title = "Glacier Dawn"
    comic.summary = "Canyon echo"
    session.commit()
    assert _match(engine, "Falcon") == []
    assert _match(engine, "Harbor") == []
    assert _match(engine, "Glacier") == [comic_id]
    assert _match(engine, "Canyon") == [comic_id]
    assert comic_id in _match(engine, "Zephyr")

    session.delete(comic)
    session.commit()
    assert _match(engine, "Glacier") == []
    assert _match(engine, "Canyon") == []
    assert comic_id not in _match(engine, "Zephyr")


def test_restore_triggers_migration_backfills_comics_missed_by_dropped_triggers(migrated_db):
    config, engine = migrated_db
    command.upgrade(config, REVISION_BEFORE_TRIGGER_FIX)

    # Earlier batch_alter_table("comics") rebuilds dropped the triggers.
    assert _fts_triggers(engine) == set()

    session = sessionmaker(bind=engine)()
    library, volume = _seed_volume(session, "fts-backfill-lib", "Backfill Series")
    comic_id = _add_comic(session, library, volume, "1", "Missed Comic", "Missed Comic summary").id
    session.close()

    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO comics_fts(rowid, title, series, summary) VALUES (999, 'Deleted', 'Gone', '')")
        )
    assert comic_id not in _fts_rows(engine)

    command.upgrade(config, REVISION_TRIGGER_FIX)

    assert _fts_rows(engine) == {
        comic_id: ("Missed Comic", "Backfill Series", "Missed Comic summary"),
    }


def test_contentless_migration_reindexes_existing_comics_without_storing_text(migrated_db):
    config, engine = migrated_db
    command.upgrade(config, REVISION_TRIGGER_FIX)

    session = sessionmaker(bind=engine)()
    library, volume = _seed_volume(session, "fts-reindex-lib", "Quasar Series")
    comic_id = _add_comic(session, library, volume, "1", "Existing Comic", "Lantern tale").id
    session.close()
    assert _stores_fts_content(engine)

    command.upgrade(config, "head")

    assert not _stores_fts_content(engine)
    assert _fts_triggers(engine) == FTS_TRIGGERS
    assert _match(engine, "Existing") == [comic_id]
    assert _match(engine, "Quasar") == [comic_id]
    assert _match(engine, "Lantern") == [comic_id]
    # Contentless: the text isn't stored, so the columns read back as NULL.
    assert _fts_rows(engine) == {comic_id: (None, None, None)}


def test_fts_index_at_head_is_contentless_and_tracks_comic_changes(migrated_db):
    config, engine = migrated_db
    command.upgrade(config, "head")

    assert _fts_triggers(engine) == FTS_TRIGGERS
    assert not _stores_fts_content(engine)

    session = sessionmaker(bind=engine)()
    library, volume = _seed_volume(session, "fts-sync-lib", "Zephyr Series")
    _assert_index_tracks_comic_changes(engine, session, library, volume)
    session.close()


@pytest.mark.parametrize(
    ("sqlite_version", "expect_stored_content"),
    [((3, 42, 0), True), ((3, 43, 0), False)],
)
def test_contentless_migration_falls_back_to_stored_content_before_sqlite_3_43(
    migrated_db, monkeypatch, sqlite_version, expect_stored_content
):
    config, engine = migrated_db
    command.upgrade(config, REVISION_TRIGGER_FIX)

    session = sessionmaker(bind=engine)()
    library, volume = _seed_volume(session, "fts-fallback-lib", "Zephyr Series")
    comic_id = _add_comic(session, library, volume, "1", "Existing Comic", "Lantern tale").id
    library_type, library_id, volume_id = type(library), library.id, volume.id
    session.close()

    migration = _load_contentless_fts_migration()
    monkeypatch.setattr(migration, "_sqlite_version", lambda bind: sqlite_version)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()

    assert _stores_fts_content(engine) is expect_stored_content
    assert _fts_triggers(engine) == FTS_TRIGGERS
    assert _match(engine, "Existing") == [comic_id]

    session = sessionmaker(bind=engine)()
    library, volume = session.get(library_type, library_id), session.get(Volume, volume_id)
    _assert_index_tracks_comic_changes(engine, session, library, volume)
    session.close()
