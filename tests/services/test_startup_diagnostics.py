import logging
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.security import get_password_hash
from app.models.comic import Comic, Volume
from app.models.job import JobStatus, JobType, ScanJob
from app.models.library_root import LibraryRoot
from app.models.series import Series
from app.models.user import User
from tests.factories import create_library_with_root
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
    library = create_library_with_root(db, "Main Library", "/comics/main")
    root = library.active_root
    series = Series(name="Amazing Tales", library=library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="1",
        title="Amazing Tales #1",
        filename="amazing-tales-1.cbz",
        library_root_id=root.id,
        relative_path="Amazing Tales #1.cbz",
    )

    db.add_all([user, series, volume, comic])
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
    assert any("Main Library" in record.message and "root_count" in record.message for record in caplog.records)
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


def test_collect_startup_diagnostics_detects_legacy_default_admin_password(db, tmp_path):
    db.add(
        User(
            username="admin",
            email="admin@example.com",
            hashed_password=get_password_hash("admin"),
            is_superuser=True,
            is_active=True,
        )
    )
    db.commit()

    diagnostics = collect_startup_diagnostics(
        db,
        database_url=f"sqlite:///{(tmp_path / 'comics.db').as_posix()}",
        comics_root=tmp_path / "probe",
        include_security_checks=True,
    )

    assert diagnostics["legacy_default_admin_password_active"] is True


def test_collect_startup_diagnostics_ignores_changed_admin_password(db, tmp_path):
    db.add(
        User(
            username="admin",
            email="admin@example.com",
            hashed_password=get_password_hash("changed-password"),
            is_superuser=True,
            is_active=True,
        )
    )
    db.commit()

    diagnostics = collect_startup_diagnostics(
        db,
        database_url=f"sqlite:///{(tmp_path / 'comics.db').as_posix()}",
        comics_root=tmp_path / "probe",
        include_security_checks=True,
    )

    assert diagnostics["legacy_default_admin_password_active"] is False


def test_collect_startup_diagnostics_includes_privacy_safe_recent_jobs(db, tmp_path):
    base_time = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    db.add(
        ScanJob(
            library_id=12345,
            job_type=JobType.SCAN,
            status=JobStatus.FAILED,
            force_scan=True,
            created_at=base_time - timedelta(minutes=3),
            started_at=base_time - timedelta(minutes=2),
            completed_at=base_time,
            result_summary=json.dumps({
                "imported": 2,
                "updated": 1,
                "elapsed": 120.5,
                "force_scan_recommended": True,
                "library_name": "Private Library",
                "path": "C:/Users/test/Comics/Private Library",
                "error_details": ["C:/Users/test/Comics/Private Library/Secret #1.cbz"],
            }),
            error_message="Failed reading C:/Users/test/Comics/Private Library/Secret #1.cbz",
        )
    )
    db.commit()

    diagnostics = collect_startup_diagnostics(
        db,
        database_url=f"sqlite:///{(tmp_path / 'comics.db').as_posix()}",
        comics_root=tmp_path / "probe",
    )

    recent_job = diagnostics["recent_jobs"][0]
    assert recent_job["job_type"] == "scan"
    assert recent_job["status"] == "failed"
    assert recent_job["scope"] == "library"
    assert recent_job["force_scan"] is True
    assert recent_job["has_error"] is True
    assert recent_job["duration_seconds"] == 120.0
    assert recent_job["summary"] == {
        "imported": 2,
        "updated": 1,
        "elapsed": 120.5,
        "force_scan_recommended": True,
    }
    assert "library_id" not in recent_job
    assert "error_message" not in recent_job

    serialized_job = json.dumps(recent_job)
    assert "Private Library" not in serialized_job
    assert "C:/Users/test/Comics" not in serialized_job
    assert "Secret #1.cbz" not in serialized_job


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


def test_build_home_startup_notice_returns_admin_legacy_password_warning():
    diagnostics = {
        "status": STARTUP_STATUS_HEALTHY,
        "status_title": "Healthy",
        "status_summary": "Healthy summary",
        "recommended_actions": [],
        "legacy_default_admin_password_active": True,
    }

    notice = build_home_startup_notice(diagnostics, is_admin=True)

    assert notice is not None
    assert notice["status"] == "legacy_default_admin_password_active"
    assert notice["primary_action_url"] == "/user/settings"
    assert "legacy default password" in notice["summary"]
    assert build_home_startup_notice(diagnostics, is_admin=False) is None


def test_build_support_snapshot_wraps_diagnostics_with_metadata():
    diagnostics = {
        "status": "healthy",
        "status_title": "Healthy",
        "status_summary": "Everything looks good.",
        "is_suspicious": False,
        "runtime": {"mode": RUNTIME_MODE_LOCAL, "label": "Local filesystem"},
        "database": {
            "url": "sqlite:///C:/Users/test/Parker/storage/database/comics.db",
            "url_display": "sqlite:///C:/.../comics.db",
            "path": "C:/Users/test/Parker/storage/database/comics.db",
            "path_display": "C:/.../comics.db",
            "exists": True,
            "size_bytes": 128,
            "size_display": "128 B",
            "wal_size_bytes": None,
            "wal_size_display": None,
            "shm_size_bytes": None,
            "shm_size_display": None,
            "alembic_version": "head",
        },
        "counts": {"users": 1, "libraries": 2, "series": 3, "comics": 4},
        "default_admin_present": True,
        "library_sample": [
            {
                "name": "Main",
                "path": "C:/Users/test/Comics/Main",
                "path_display": "C:/.../Main",
                "path_exists": True,
                "root_count": 1,
                "active_root_count": 1,
                "roots": [
                    {
                        "id": 42,
                        "path": "C:/Users/test/Comics/Main",
                        "path_display": "C:/.../Main",
                        "is_active": True,
                        "path_exists": True,
                    }
                ],
            }
        ],
        "recent_jobs": [{"job_type": "scan", "status": "completed"}],
        "comics_root": {
            "path": "C:/Users/test/Comics",
            "path_display": "C:/.../Comics",
            "exists": True,
            "sample": ["Private Folder/"],
            "sample_count": 1,
        },
        "recommended_actions": [],
    }

    snapshot = build_support_snapshot(
        diagnostics,
        app_version="0.1.18",
        git_commit_hash="abc123def456",
        generated_at=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
    )

    assert snapshot["snapshot_type"] == "parker_startup_diagnostics"
    assert snapshot["schema_version"] == 1
    assert snapshot["generated_at_utc"] == "2026-07-13T12:00:00+00:00"
    assert snapshot["app_version"] == "0.1.18"
    assert snapshot["build"] == {
        "app_version": "0.1.18",
        "git_commit_hash": "abc123def456",
    }
    assert snapshot["status"]["code"] == "healthy"
    assert snapshot["database"]["url"] == "sqlite:///C:/.../comics.db"
    assert snapshot["database"]["path"] == "C:/.../comics.db"
    assert snapshot["database"]["paths_redacted"] is True
    assert snapshot["configured_library_sample"] == [
        {
            "name": "Main",
            "path": "C:/.../Main",
            "path_exists": True,
            "root_count": 1,
            "active_root_count": 1,
            "roots": [
                {
                    "path": "C:/.../Main",
                    "is_active": True,
                    "path_exists": True,
                }
            ],
            "paths_redacted": True,
        }
    ]
    assert snapshot["comics_probe"] == {
        "path": "C:/.../Comics",
        "exists": True,
        "sample_count": 1,
        "sample_names_redacted": True,
        "paths_redacted": True,
    }
    assert snapshot["recent_jobs"] == [{"job_type": "scan", "status": "completed"}]
    assert "legacy_default_admin_password_active" not in snapshot

    serialized_snapshot = json.dumps(snapshot)
    assert "C:/Users/test" not in serialized_snapshot
    assert "Private Folder" not in serialized_snapshot


def test_collect_startup_diagnostics_marks_default_comics_root_as_container_runtime(db, tmp_path):
    create_library_with_root(db, "Container Library", "/comics/main")
    db.commit()

    db_path = tmp_path / "comics.db"
    db_path.write_bytes(b"x" * 128)

    diagnostics = collect_startup_diagnostics(
        db,
        database_url=f"sqlite:///{db_path.as_posix()}",
    )

    assert diagnostics["runtime"]["mode"] == RUNTIME_MODE_CONTAINER


def test_collect_startup_diagnostics_marks_missing_default_comics_root_as_local_when_library_paths_are_local(db, tmp_path):
    create_library_with_root(db, "Local Library", "C:/Users/test/MyComics")
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

    create_library_with_root(db, "Existing", str(existing_library_root))
    create_library_with_root(db, "Missing", str(tmp_path / "DoesNotExist"))
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


def test_collect_startup_diagnostics_adds_redacted_display_paths(db):
    create_library_with_root(db, "Private Path Library", "D:/_ComicTests/DC2")
    db.commit()

    diagnostics = collect_startup_diagnostics(
        db,
        database_url="sqlite:///D:/Parker/storage/database/comics.db",
        comics_root=Path("D:/_ComicTests"),
    )

    assert diagnostics["database"]["url"] == "sqlite:///D:/Parker/storage/database/comics.db"
    assert diagnostics["database"]["url_display"] == "sqlite:///D:/.../comics.db"
    assert diagnostics["database"]["path_display"] == "D:/.../comics.db"
    assert diagnostics["library_sample"][0]["path"] == "D:/_ComicTests/DC2"
    assert diagnostics["library_sample"][0]["path_display"] == "D:/.../DC2"
    assert diagnostics["library_sample"][0]["roots"][0]["path_display"] == "D:/.../DC2"
    assert diagnostics["comics_root"]["path_display"] == "D:/.../_ComicTests"


def test_collect_startup_diagnostics_redacts_database_url_credentials(db, tmp_path):
    diagnostics = collect_startup_diagnostics(
        db,
        database_url="postgresql://reader:secret-password@db.internal:5432/private_parker?sslmode=require",
        comics_root=tmp_path / "probe",
    )

    assert diagnostics["database"]["url_display"] == "postgresql://<credentials>@db.internal:5432/.../private_parker"
    assert "reader" not in diagnostics["database"]["url_display"]
    assert "secret-password" not in diagnostics["database"]["url_display"]
    assert "sslmode" not in diagnostics["database"]["url_display"]


def test_collect_startup_diagnostics_reports_multiple_library_roots(db, tmp_path):
    first_root = tmp_path / "First"
    second_root = tmp_path / "Second"
    first_root.mkdir()

    library = create_library_with_root(db, "Multi Root", str(first_root))
    db.add(LibraryRoot(library_id=library.id, path=str(second_root), is_active=True))
    db.commit()

    db_path = tmp_path / "comics.db"
    db_path.write_bytes(b"x" * 128)

    diagnostics = collect_startup_diagnostics(
        db,
        database_url=f"sqlite:///{db_path.as_posix()}",
        comics_root=tmp_path / "probe",
    )

    sample = diagnostics["library_sample"][0]
    assert sample["name"] == "Multi Root"
    assert sample["root_count"] == 2
    assert sample["active_root_count"] == 2
    assert [root["path_exists"] for root in sample["roots"]] == [True, False]
