import inspect
import multiprocessing
import os
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from queue import Empty

import pytest
from fastapi import HTTPException

import app.api.migration as migration_api
import app.services.kavita_migration_staging as migration_staging
from app.models.user import User


PREVIEW_PAYLOAD = {
    "users": [
        {
            "kavita_user_id": 1,
            "username": "existing-user",
            "is_new": False,
            "is_admin": False,
            "importable_progress": 1,
        },
        {
            "kavita_user_id": 2,
            "username": "new-user",
            "is_new": True,
            "is_admin": False,
            "importable_progress": 1,
        },
    ],
    "progress": {"source_records": 3, "importable_records": 2, "skipped_records": 1},
    "mapping": {"mapped_total": 2},
}


@pytest.fixture(autouse=True)
def migration_cache(monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(migration_staging, "STAGED_DATABASE_DIR", cache_dir)
    monkeypatch.setattr(migration_staging, "PREVIEW_CACHE_FILE", cache_dir / "kavita_migration_previews.json")
    monkeypatch.setattr(migration_staging, "PREVIEW_LOCK_FILE", cache_dir / "kavita_migration_previews.lock")
    return cache_dir


def _consume_preview_in_other_process(
    staged_database_dir: str,
    preview_cache_file: str,
    preview_lock_file: str,
    token: str,
    admin_id: int,
    result_queue,
) -> None:
    import app.services.kavita_migration_staging as worker_migration

    worker_migration.STAGED_DATABASE_DIR = Path(staged_database_dir)
    worker_migration.PREVIEW_CACHE_FILE = Path(preview_cache_file)
    worker_migration.PREVIEW_LOCK_FILE = Path(preview_lock_file)
    preview = worker_migration.consume_preview(token, admin_id)
    result_queue.put(preview is not None)


def _upload_payload(user_strategy="temp-password"):
    return {
        "data": {"user_strategy": user_strategy},
        "files": {"kavita_db_file": ("kavita.db", b"fake-db-bytes", "application/octet-stream")},
    }


def _create_preview(admin_client, user_strategy="temp-password"):
    payload = _upload_payload(user_strategy=user_strategy)
    response = admin_client.post("/api/migration/preview", data=payload["data"], files=payload["files"])
    assert response.status_code == 200
    return response.json()


def test_migration_preview_rejects_unsupported_strategy(admin_client):
    payload = _upload_payload(user_strategy="first-login")

    response = admin_client.post("/api/migration/preview", data=payload["data"], files=payload["files"])

    assert response.status_code == 400
    assert "only 'temp-password'" in response.json()["detail"].lower()


def test_migration_run_requires_available_preview(admin_client):
    response = admin_client.post("/api/migration/run", data={"preview_token": "missing-preview"})

    assert response.status_code == 409
    assert "preview is no longer available" in response.json()["detail"].lower()


def test_migration_endpoints_run_synchronously():
    assert not inspect.iscoroutinefunction(migration_api.preview_kavita_migration)
    assert not inspect.iscoroutinefunction(migration_api.discard_kavita_migration_preview)
    assert not inspect.iscoroutinefunction(migration_api.run_kavita_migration)


def test_stage_kavita_database_copies_upload_in_chunks(migration_cache):
    chunk_size = 1024 * 1024

    class ChunkTrackingSource(BytesIO):
        def __init__(self, data: bytes):
            super().__init__(data)
            self.read_sizes: list[int] = []

        def read(self, size: int = -1) -> bytes:
            self.read_sizes.append(size)
            if size != chunk_size:
                raise AssertionError("The entire upload must not be read into memory at once.")
            return super().read(size)

    source_data = b"a" * (chunk_size + 17)
    source = ChunkTrackingSource(source_data)
    staged_path = migration_staging.stage_kavita_database(source)

    assert Path(staged_path).read_bytes() == source_data
    assert source.read_sizes == [chunk_size, chunk_size, chunk_size]
    migration_staging.remove_staged_database(staged_path)


def test_migration_preview_registry_can_be_consumed_by_another_worker(migration_cache):
    staged_database = migration_cache / "parker-kavita-preview-shared.db"
    staged_database.parent.mkdir(parents=True)
    staged_database.write_bytes(b"fake-db-bytes")
    token, _ = migration_staging.store_preview(admin_id=42, db_path=str(staged_database))

    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    process = context.Process(
        target=_consume_preview_in_other_process,
        args=(
            str(migration_staging.STAGED_DATABASE_DIR),
            str(migration_staging.PREVIEW_CACHE_FILE),
            str(migration_staging.PREVIEW_LOCK_FILE),
            token,
            42,
            result_queue,
        ),
    )
    process.start()
    try:
        assert result_queue.get(timeout=10) is True
    except Empty as exc:
        raise AssertionError("Timed out waiting for the migration preview worker") from exc
    finally:
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    assert process.exitcode == 0
    assert migration_staging.consume_preview(token, admin_id=42) is None


def test_cleanup_expired_staged_previews_removes_expired_and_orphaned_files(migration_cache):
    expired_database = migration_cache / "parker-kavita-preview-expired.db"
    orphaned_database = migration_cache / "parker-kavita-preview-orphaned.db"
    migration_cache.mkdir(parents=True)
    expired_database.write_bytes(b"expired")
    orphaned_database.write_bytes(b"orphaned")

    migration_staging._write_staged_previews(
        {
            "expired-token": migration_staging.StagedMigrationPreview(
                admin_id=42,
                db_path=str(expired_database),
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
        }
    )
    stale_timestamp = time.time() - migration_staging.PREVIEW_EXPIRY.total_seconds() - 1
    os.utime(orphaned_database, (stale_timestamp, stale_timestamp))

    migration_staging.cleanup_expired_staged_previews()

    assert not expired_database.exists()
    assert not orphaned_database.exists()
    assert migration_staging._read_staged_previews() == {}


def test_migration_preview_stages_database_and_run_returns_csv(admin_client, monkeypatch):
    class FakeMigrationService:
        paths = []

        def __init__(self, db, kavita_db_path):
            self.paths.append(kavita_db_path)

        def preview_migration(self):
            return PREVIEW_PAYLOAD

        def migrate_users(self, strategy="temp-password", kavita_user_ids=None):
            return "username,temporary_password,email,role\nuser1,pwd,test@example.com,User\n"

        def migrate_progress(self):
            return {"inserted": 1, "updated": 0, "skipped": 0}

        def close(self):
            pass

    monkeypatch.setattr("app.api.migration.KavitaMigrationService", FakeMigrationService)

    preview = _create_preview(admin_client)
    staged_path = Path(FakeMigrationService.paths[0])

    assert preview["users"] == PREVIEW_PAYLOAD["users"]
    assert preview["progress"] == PREVIEW_PAYLOAD["progress"]
    assert preview["preview_token"]
    assert preview["expires_at"]
    assert staged_path.exists()

    response = admin_client.post(
        "/api/migration/run",
        data={"preview_token": preview["preview_token"], "user_strategy": "temp-password"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=parker_migrated_credentials_" in response.headers["content-disposition"]
    assert "username,temporary_password" in response.text
    assert FakeMigrationService.paths == [str(staged_path), str(staged_path)]
    assert not staged_path.exists()


def test_migration_preview_discard_removes_staged_database(admin_client, monkeypatch):
    class FakeMigrationService:
        paths = []

        def __init__(self, db, kavita_db_path):
            self.paths.append(kavita_db_path)

        def preview_migration(self):
            return PREVIEW_PAYLOAD

        def close(self):
            pass

    monkeypatch.setattr("app.api.migration.KavitaMigrationService", FakeMigrationService)

    preview = _create_preview(admin_client)
    staged_path = Path(FakeMigrationService.paths[0])

    response = admin_client.post(
        "/api/migration/preview/discard",
        data={"preview_token": preview["preview_token"]},
    )

    assert response.status_code == 200
    assert not staged_path.exists()

    run_response = admin_client.post("/api/migration/run", data={"preview_token": preview["preview_token"]})
    assert run_response.status_code == 409


@pytest.mark.parametrize(
    ("run_data", "expected_user_ids"),
    [
        ({"kavita_user_ids": ["1", "3"]}, [1, 3]),
        ({}, None),
    ],
)
def test_migration_run_passes_selected_kavita_user_ids(admin_client, monkeypatch, run_data, expected_user_ids):
    class FakeMigrationService:
        received_user_ids = []

        def __init__(self, db, kavita_db_path):
            pass

        def preview_migration(self):
            return PREVIEW_PAYLOAD

        def migrate_users(self, strategy="temp-password", kavita_user_ids=None):
            self.received_user_ids.append(kavita_user_ids)
            return None

        def migrate_progress(self):
            return {"inserted": 0, "updated": 0, "skipped": 0}

        def close(self):
            pass

    monkeypatch.setattr("app.api.migration.KavitaMigrationService", FakeMigrationService)

    preview = _create_preview(admin_client)
    response = admin_client.post(
        "/api/migration/run",
        data={"preview_token": preview["preview_token"], **run_data},
    )

    assert response.status_code == 200
    assert FakeMigrationService.received_user_ids == [expected_user_ids]


def test_migration_run_returns_json_when_no_csv(admin_client, monkeypatch):
    class FakeMigrationService:
        def __init__(self, db, kavita_db_path):
            pass

        def preview_migration(self):
            return PREVIEW_PAYLOAD

        def migrate_users(self, strategy="temp-password", kavita_user_ids=None):
            return None

        def migrate_progress(self):
            return {"inserted": 2, "updated": 3, "skipped": 4}

        def close(self):
            pass

    monkeypatch.setattr("app.api.migration.KavitaMigrationService", FakeMigrationService)

    preview = _create_preview(admin_client)
    response = admin_client.post("/api/migration/run", data={"preview_token": preview["preview_token"]})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "Migration of users and reading progress complete."
    assert body["details"] == {"inserted": 2, "updated": 3, "skipped": 4}


def test_migration_run_rolls_back_when_progress_phase_fails(admin_client, db, monkeypatch):
    class FailingMigrationService:
        def __init__(self, db, kavita_db_path):
            self.db = db

        def preview_migration(self):
            return PREVIEW_PAYLOAD

        def migrate_users(self, strategy="temp-password", kavita_user_ids=None):
            self.db.add(
                User(
                    username="rollback-user",
                    email="rollback-user@example.com",
                    hashed_password="hash",
                    is_superuser=False,
                    is_active=True,
                )
            )
            self.db.flush()
            return None

        def migrate_progress(self):
            raise RuntimeError("simulated migration failure")

        def close(self):
            pass

    monkeypatch.setattr("app.api.migration.KavitaMigrationService", FailingMigrationService)

    preview = _create_preview(admin_client)
    response = admin_client.post("/api/migration/run", data={"preview_token": preview["preview_token"]})

    assert response.status_code == 500
    assert "critical error" in response.json()["detail"].lower()

    rolled_back = db.query(User).filter(User.username == "rollback-user").first()
    assert rolled_back is None


@pytest.mark.parametrize(
    "content",
    [b"hello, this is not a database", b""],
    ids=["text-file", "empty-file"],
)
def test_migration_preview_rejects_non_kavita_upload_with_400(admin_client, migration_cache, content):
    response = admin_client.post(
        "/api/migration/preview",
        data={"user_strategy": "temp-password"},
        files={"kavita_db_file": ("kavita.db", content, "application/octet-stream")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "This file isn't a Kavita database. Upload the kavita.db file from your Kavita config folder."
    )
    assert list(migration_cache.glob("*.db")) == []


def test_migration_preview_maps_file_not_found_to_404(admin_client, monkeypatch):
    class MissingKavitaDbService:
        def __init__(self, db, kavita_db_path):
            raise FileNotFoundError("kavita.db not found")

    monkeypatch.setattr("app.api.migration.KavitaMigrationService", MissingKavitaDbService)

    payload = _upload_payload()
    response = admin_client.post("/api/migration/preview", data=payload["data"], files=payload["files"])

    assert response.status_code == 404
    assert "file error" in response.json()["detail"].lower()


def test_migration_run_reraises_http_exception_from_inner_flow(admin_client, monkeypatch):
    class HttpFailingMigrationService:
        def __init__(self, db, kavita_db_path):
            pass

        def preview_migration(self):
            return PREVIEW_PAYLOAD

        def migrate_users(self, strategy="temp-password", kavita_user_ids=None):
            raise HTTPException(status_code=418, detail="teapot")

        def migrate_progress(self):
            return {"inserted": 0, "updated": 0, "skipped": 0}

        def close(self):
            pass

    monkeypatch.setattr("app.api.migration.KavitaMigrationService", HttpFailingMigrationService)

    preview = _create_preview(admin_client)
    response = admin_client.post("/api/migration/run", data={"preview_token": preview["preview_token"]})

    assert response.status_code == 418
    assert response.json() == {"detail": "teapot"}


def test_migration_run_ignores_permission_error_while_cleaning_staged_file(admin_client, monkeypatch):
    class SuccessMigrationService:
        paths = []

        def __init__(self, db, kavita_db_path):
            self.paths.append(kavita_db_path)

        def preview_migration(self):
            return PREVIEW_PAYLOAD

        def migrate_users(self, strategy="temp-password", kavita_user_ids=None):
            return None

        def migrate_progress(self):
            return {"inserted": 0, "updated": 0, "skipped": 0}

        def close(self):
            pass

    monkeypatch.setattr("app.api.migration.KavitaMigrationService", SuccessMigrationService)

    preview = _create_preview(admin_client)

    def raise_permission_error(_path):
        raise PermissionError("in use")

    monkeypatch.setattr("app.services.kavita_migration_staging.os.remove", raise_permission_error)

    response = admin_client.post("/api/migration/run", data={"preview_token": preview["preview_token"]})

    assert response.status_code == 200
    assert response.json()["status"] == "Migration of users and reading progress complete."

    monkeypatch.undo()
    Path(SuccessMigrationService.paths[0]).unlink(missing_ok=True)
