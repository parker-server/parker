import logging
from pathlib import Path

from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.series import Series
from app.models.user import User
from app.services.startup_diagnostics import (
    log_startup_diagnostics,
    resolve_sqlite_db_path,
)


def test_resolve_sqlite_db_path_supports_relative_and_absolute_paths():
    assert resolve_sqlite_db_path("sqlite:///./storage/database/comics.db") == Path("./storage/database/comics.db")
    assert resolve_sqlite_db_path("sqlite:////app/storage/database/comics.db") == Path("/app/storage/database/comics.db")
    assert resolve_sqlite_db_path("postgresql://user:pass@host/db") is None


def test_log_startup_diagnostics_warns_for_effectively_empty_database(db, caplog, tmp_path):
    db.add(
        User(
            username="admin",
            email="admin@example.com",
            hashed_password="fakehash",
            is_superuser=True,
            is_active=True,
        )
    )
    db.commit()

    db_path = tmp_path / "comics.db"
    db_path.write_bytes(b"x" * 432)
    (tmp_path / "comics.db-shm").write_bytes(b"x" * 32)
    (tmp_path / "comics.db-wal").write_bytes(b"")

    comics_root = tmp_path / "comics"
    comics_root.mkdir()
    (comics_root / "Marvel").mkdir()

    caplog.set_level(logging.INFO, logger="app.startup")

    log_startup_diagnostics(
        db,
        database_url=f"sqlite:///{db_path.as_posix()}",
        comics_root=comics_root,
    )

    assert any("counts users=1 libraries=0 series=0 comics=0" in record.message for record in caplog.records)
    assert any(
        "effectively empty database" in record.message
        for record in caplog.records
        if record.levelno == logging.WARNING
    )
    assert any(
        "database has no libraries configured" in record.message
        for record in caplog.records
        if record.levelno == logging.WARNING
    )


def test_log_startup_diagnostics_logs_populated_database_summary(db, caplog, tmp_path):
    user = User(
        username="admin",
        email="admin@example.com",
        hashed_password="fakehash",
        is_superuser=True,
        is_active=True,
    )
    library = Library(name="Main Library", path="/comics/main")
    series = Series(name="Amazing Tales", library=library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="1",
        title="Amazing Tales #1",
        filename="amazing-tales-1.cbz",
        file_path="/comics/main/Amazing Tales #1.cbz",
    )

    db.add_all([user, library, series, volume, comic])
    db.commit()

    db_path = tmp_path / "comics.db"
    db_path.write_bytes(b"x" * 872)

    caplog.set_level(logging.INFO, logger="app.startup")

    log_startup_diagnostics(
        db,
        database_url=f"sqlite:///{db_path.as_posix()}",
        comics_root=tmp_path / "comics",
    )

    assert any("counts users=1 libraries=1 series=1 comics=1" in record.message for record in caplog.records)
    assert any("library_sample=[{'name': 'Main Library', 'path': '/comics/main'}]" in record.message for record in caplog.records)
    assert not any(
        "effectively empty database" in record.message
        for record in caplog.records
        if record.levelno == logging.WARNING
    )
