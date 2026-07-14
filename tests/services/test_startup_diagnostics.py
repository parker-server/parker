import logging
from datetime import datetime, timezone
from pathlib import Path

from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.series import Series
from app.models.user import User
from app.services.startup_diagnostics import (
    RUNTIME_MODE_CONTAINER,
    RUNTIME_MODE_LOCAL,
    STARTUP_STATUS_HEALTHY,
    STARTUP_STATUS_STORAGE_MISMATCH,
    build_home_startup_notice,
    build_support_snapshot,
    collect_startup_diagnostics,
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

    assert any(
        "status=storage_mismatch_suspected counts users=1 libraries=0 series=0 comics=0" in record.message
        for record in caplog.records
    )
    assert any(
        "active database has no libraries configured" in record.message
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

    assert any("status=healthy counts users=1 libraries=1 series=1 comics=1" in record.message for record in caplog.records)
    assert any(
        "library_sample=[{'name': 'Main Library', 'path': '/comics/main', 'path_exists': False}]"
        in record.message
        for record in caplog.records
    )
    assert not any(
        "active database has no libraries configured" in record.message
        for record in caplog.records
        if record.levelno == logging.WARNING
    )


def test_collect_startup_diagnostics_classifies_storage_mismatch(db, tmp_path):
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
    comics_root = tmp_path / "comics"
    comics_root.mkdir()
    (comics_root / "DC").mkdir()

    diagnostics = collect_startup_diagnostics(
        db,
        database_url=f"sqlite:///{db_path.as_posix()}",
        comics_root=comics_root,
    )

    assert diagnostics["status"] == STARTUP_STATUS_STORAGE_MISMATCH
    assert diagnostics["is_suspicious"] is True
    assert diagnostics["recommended_actions"]
    assert diagnostics["runtime"]["mode"] == RUNTIME_MODE_LOCAL
    assert diagnostics["database"]["size_display"] == "432 B"


def test_build_home_startup_notice_returns_admin_notice_for_storage_mismatch():
    diagnostics = {
        "status": STARTUP_STATUS_STORAGE_MISMATCH,
        "status_title": "Storage Mismatch Suspected",
        "status_summary": "Mismatch summary",
        "recommended_actions": ["Check storage"],
    }

    notice = build_home_startup_notice(diagnostics, is_admin=True)

    assert notice is not None
    assert notice["diagnostics_url"] == "/admin/diagnostics"
    assert notice["is_admin"] is True


def test_build_home_startup_notice_ignores_healthy_state():
    diagnostics = {
        "status": STARTUP_STATUS_HEALTHY,
        "status_title": "Healthy",
        "status_summary": "Healthy summary",
        "recommended_actions": [],
    }

    assert build_home_startup_notice(diagnostics, is_admin=True) is None


def test_build_support_snapshot_wraps_diagnostics_with_metadata():
    diagnostics = {
        "status": "healthy",
        "status_title": "Healthy",
        "status_summary": "Everything looks good.",
        "is_suspicious": False,
        "runtime": {"mode": RUNTIME_MODE_LOCAL, "label": "Local filesystem"},
        "database": {"path": "/tmp/comics.db"},
        "counts": {"users": 1, "libraries": 2, "series": 3, "comics": 4},
        "default_admin_present": True,
        "library_sample": [{"name": "Main", "path": "C:/Comics"}],
        "comics_root": {"path": "/comics", "exists": False, "sample": []},
        "recommended_actions": [],
    }

    snapshot = build_support_snapshot(
        diagnostics,
        app_version="0.1.18",
        generated_at=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
    )

    assert snapshot["snapshot_type"] == "parker_startup_diagnostics"
    assert snapshot["schema_version"] == 1
    assert snapshot["generated_at_utc"] == "2026-07-13T12:00:00+00:00"
    assert snapshot["app_version"] == "0.1.18"
    assert snapshot["status"]["code"] == "healthy"
    assert snapshot["configured_library_sample"] == [{"name": "Main", "path": "C:/Comics"}]


def test_collect_startup_diagnostics_marks_default_comics_root_as_container_runtime(db, tmp_path):
    library = Library(name="Container Library", path="/comics/main")
    db.add(library)
    db.commit()

    db_path = tmp_path / "comics.db"
    db_path.write_bytes(b"x" * 128)

    diagnostics = collect_startup_diagnostics(
        db,
        database_url=f"sqlite:///{db_path.as_posix()}",
    )

    assert diagnostics["runtime"]["mode"] == RUNTIME_MODE_CONTAINER


def test_collect_startup_diagnostics_marks_missing_default_comics_root_as_local_when_library_paths_are_local(db, tmp_path):
    library = Library(name="Local Library", path="C:/Users/test/MyComics")
    db.add(library)
    db.commit()

    db_path = tmp_path / "comics.db"
    db_path.write_bytes(b"x" * 128)

    diagnostics = collect_startup_diagnostics(
        db,
        database_url=f"sqlite:///{db_path.as_posix()}",
    )

    assert diagnostics["runtime"]["mode"] == RUNTIME_MODE_LOCAL


def test_collect_startup_diagnostics_tracks_library_path_existence(db, tmp_path):
    existing_library_root = tmp_path / "Comics"
    existing_library_root.mkdir()

    db.add_all([
        Library(name="Existing", path=str(existing_library_root)),
        Library(name="Missing", path=str(tmp_path / "DoesNotExist")),
    ])
    db.commit()

    db_path = tmp_path / "comics.db"
    db_path.write_bytes(b"x" * 2048)

    diagnostics = collect_startup_diagnostics(
        db,
        database_url=f"sqlite:///{db_path.as_posix()}",
        comics_root=tmp_path / "probe",
    )

    by_name = {item["name"]: item for item in diagnostics["library_sample"]}
    assert by_name["Existing"]["path_exists"] is True
    assert by_name["Missing"]["path_exists"] is False
    assert diagnostics["database"]["size_display"] == "2.0 KB"
