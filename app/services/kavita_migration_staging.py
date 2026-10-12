"""Shared, short-lived staging for Kavita migration previews."""

import json
import logging
import os
import secrets
import shutil
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO, Iterator

import portalocker

from app.config import settings


logger = logging.getLogger(__name__)

PREVIEW_EXPIRY = timedelta(minutes=30)
STAGED_DATABASE_PREFIX = "parker-kavita-preview-"
STAGED_DATABASE_DIR = settings.cache_dir
PREVIEW_CACHE_FILE = settings.cache_dir / "kavita_migration_previews.json"
PREVIEW_LOCK_FILE = settings.cache_dir / "kavita_migration_previews.lock"
_preview_thread_lock = threading.Lock()


@dataclass(frozen=True)
class StagedMigrationPreview:
    admin_id: int
    db_path: str
    expires_at: datetime


def _is_staged_database_path(path: Path) -> bool:
    try:
        return (
            path.resolve().parent == STAGED_DATABASE_DIR.resolve()
            and path.name.startswith(STAGED_DATABASE_PREFIX)
            and path.suffix == ".db"
        )
    except OSError:
        return False


def _remove_staged_file(path: str) -> None:
    staged_path = Path(path)
    if not _is_staged_database_path(staged_path):
        logger.warning("Refusing to remove unexpected staged Kavita database path: %s", path)
        return

    try:
        os.remove(staged_path)
    except FileNotFoundError:
        pass
    except PermissionError:
        logger.warning("Could not remove staged Kavita migration database: %s", path)


def _preview_from_dict(payload: object) -> StagedMigrationPreview | None:
    if not isinstance(payload, dict):
        return None

    admin_id = payload.get("admin_id")
    db_path = payload.get("db_path")
    expires_at = payload.get("expires_at")
    if isinstance(admin_id, bool) or not isinstance(admin_id, int):
        return None
    if not isinstance(db_path, str) or not isinstance(expires_at, str):
        return None

    try:
        expires_at_value = datetime.fromisoformat(expires_at)
    except ValueError:
        return None
    if expires_at_value.tzinfo is None or not _is_staged_database_path(Path(db_path)):
        return None

    return StagedMigrationPreview(
        admin_id=admin_id,
        db_path=db_path,
        expires_at=expires_at_value.astimezone(timezone.utc),
    )


def _read_staged_previews() -> dict[str, StagedMigrationPreview]:
    try:
        payload = json.loads(PREVIEW_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(payload, dict) or not isinstance(payload.get("previews"), dict):
        return {}

    previews: dict[str, StagedMigrationPreview] = {}
    for token, preview_payload in payload["previews"].items():
        if not isinstance(token, str):
            continue
        preview = _preview_from_dict(preview_payload)
        if preview is not None:
            previews[token] = preview
    return previews


def _write_staged_previews(previews: dict[str, StagedMigrationPreview]) -> None:
    if not previews:
        try:
            PREVIEW_CACHE_FILE.unlink()
        except FileNotFoundError:
            pass
        return

    PREVIEW_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file_descriptor, temp_file_path = tempfile.mkstemp(
        dir=PREVIEW_CACHE_FILE.parent,
        prefix=f".{PREVIEW_CACHE_FILE.stem}-",
        suffix=".tmp",
    )
    try:
        with os.fdopen(temp_file_descriptor, "w", encoding="utf-8") as temp_file:
            json.dump(
                {
                    "previews": {
                        token: {
                            "admin_id": preview.admin_id,
                            "db_path": preview.db_path,
                            "expires_at": preview.expires_at.isoformat(),
                        }
                        for token, preview in previews.items()
                    }
                },
                temp_file,
                sort_keys=True,
                separators=(",", ":"),
            )
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_file_path, PREVIEW_CACHE_FILE)
    finally:
        try:
            os.unlink(temp_file_path)
        except FileNotFoundError:
            pass


@contextmanager
def _shared_preview_lock(*, nonblocking: bool = False) -> Iterator[None]:
    PREVIEW_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_flags = portalocker.LOCK_EX | (portalocker.LOCK_NB if nonblocking else 0)

    with _preview_thread_lock:
        with PREVIEW_LOCK_FILE.open("a+", encoding="utf-8") as lock_file:
            portalocker.lock(lock_file, lock_flags)
            try:
                yield
            finally:
                portalocker.unlock(lock_file)


def cleanup_expired_staged_previews(*, nonblocking: bool = False) -> None:
    """Remove expired or orphaned staged migration databases from the shared cache."""
    now = datetime.now(timezone.utc)
    expiry_cutoff = now.timestamp() - PREVIEW_EXPIRY.total_seconds()

    try:
        with _shared_preview_lock(nonblocking=nonblocking):
            previews = _read_staged_previews()
            active_previews = {
                token: preview for token, preview in previews.items() if preview.expires_at > now
            }
            expired_previews = [
                preview for preview in previews.values() if preview.expires_at <= now
            ]
            if len(active_previews) != len(previews):
                _write_staged_previews(active_previews)

            for preview in expired_previews:
                _remove_staged_file(preview.db_path)

            active_paths = {Path(preview.db_path).resolve() for preview in active_previews.values()}
            for staged_file in STAGED_DATABASE_DIR.glob(f"{STAGED_DATABASE_PREFIX}*.db"):
                try:
                    if staged_file.resolve() not in active_paths and staged_file.stat().st_mtime <= expiry_cutoff:
                        _remove_staged_file(str(staged_file))
                except FileNotFoundError:
                    pass
    except (OSError, portalocker.exceptions.LockException) as exc:
        log_level = logging.DEBUG if nonblocking else logging.WARNING
        logger.log(log_level, "Unable to clean staged Kavita migration databases: %s", exc)


def stage_kavita_database(source: BinaryIO) -> str:
    """Persist an uploaded database in the shared cache before it is previewed."""
    STAGED_DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    file_descriptor, temp_file_path = tempfile.mkstemp(
        dir=STAGED_DATABASE_DIR,
        prefix=STAGED_DATABASE_PREFIX,
        suffix=".db",
    )
    try:
        with os.fdopen(file_descriptor, "wb") as temp_file:
            shutil.copyfileobj(source, temp_file, length=1024 * 1024)
    except Exception:
        _remove_staged_file(temp_file_path)
        raise

    return temp_file_path


def store_preview(admin_id: int, db_path: str) -> tuple[str, datetime]:
    cleanup_expired_staged_previews()

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + PREVIEW_EXPIRY
    try:
        with _shared_preview_lock():
            previews = _read_staged_previews()
            previews[token] = StagedMigrationPreview(
                admin_id=admin_id,
                db_path=db_path,
                expires_at=expires_at,
            )
            _write_staged_previews(previews)
    except (OSError, portalocker.exceptions.LockException) as exc:
        raise RuntimeError("Unable to store the migration preview.") from exc

    return token, expires_at


def consume_preview(token: str, admin_id: int) -> StagedMigrationPreview | None:
    cleanup_expired_staged_previews()

    try:
        with _shared_preview_lock():
            previews = _read_staged_previews()
            preview = previews.get(token)
            if preview is None or preview.admin_id != admin_id:
                return None
            previews.pop(token)
            _write_staged_previews(previews)
            return preview
    except (OSError, portalocker.exceptions.LockException) as exc:
        logger.warning("Unable to consume Kavita migration preview: %s", exc)
        return None


def discard_preview(token: str, admin_id: int) -> bool:
    cleanup_expired_staged_previews()

    try:
        with _shared_preview_lock():
            previews = _read_staged_previews()
            preview = previews.get(token)
            if preview is None or preview.admin_id != admin_id:
                return False
            previews.pop(token)
            _write_staged_previews(previews)
    except (OSError, portalocker.exceptions.LockException) as exc:
        logger.warning("Unable to discard Kavita migration preview: %s", exc)
        return False

    _remove_staged_file(preview.db_path)
    return True


def remove_staged_database(path: str) -> None:
    """Best-effort removal for a staged database that was not registered."""
    _remove_staged_file(path)
