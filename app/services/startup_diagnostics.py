import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.comic import Comic
from app.models.library import Library
from app.models.series import Series
from app.models.user import User


logger = logging.getLogger("app.startup")

STARTUP_STATUS_HEALTHY = "healthy"
STARTUP_STATUS_FRESH_INSTALL = "fresh_install"
STARTUP_STATUS_EMPTY_DATABASE = "empty_database"
STARTUP_STATUS_STORAGE_MISMATCH = "storage_mismatch_suspected"
RUNTIME_MODE_CONTAINER = "container_like"
RUNTIME_MODE_LOCAL = "local_filesystem"


def resolve_sqlite_db_path(database_url: str) -> Path | None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return None

    raw_path = database_url[len(prefix):]
    return Path(raw_path)


def _safe_file_size(path: Path | None) -> int | None:
    if path is None or not path.exists():
        return None

    try:
        return path.stat().st_size
    except OSError:
        return None


def _sample_directory(path: Path, limit: int = 5) -> list[str]:
    if not path.exists() or not path.is_dir():
        return []

    sample: list[str] = []
    try:
        for entry in sorted(path.iterdir(), key=lambda item: item.name.lower()):
            label = f"{entry.name}/" if entry.is_dir() else entry.name
            sample.append(label)
            if len(sample) >= limit:
                break
    except OSError:
        return []

    return sample


def _safe_alembic_version(db: Session) -> str | None:
    try:
        row = db.execute(text("SELECT version_num FROM alembic_version")).first()
    except Exception:
        return None

    if not row:
        return None

    return row[0]


def _looks_like_container_library_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    return normalized == "/comics" or normalized.startswith("/comics/")


def _detect_runtime_mode(
    comics_root: Path,
    *,
    comics_root_exists: bool,
    library_sample: list[dict],
) -> str:
    if comics_root.as_posix() != "/comics":
        return RUNTIME_MODE_LOCAL

    if comics_root_exists:
        return RUNTIME_MODE_CONTAINER

    if any(_looks_like_container_library_path(item.get("path", "")) for item in library_sample):
        return RUNTIME_MODE_CONTAINER

    return RUNTIME_MODE_LOCAL


def _classify_startup_status(
    *,
    users_count: int,
    libraries_count: int,
    series_count: int,
    comics_count: int,
    default_admin_present: bool,
    comics_root_sample: list[str],
) -> str:
    if libraries_count == 0 and series_count == 0 and comics_count == 0:
        if default_admin_present and comics_root_sample:
            return STARTUP_STATUS_STORAGE_MISMATCH
        if default_admin_present:
            return STARTUP_STATUS_FRESH_INSTALL
        return STARTUP_STATUS_EMPTY_DATABASE

    return STARTUP_STATUS_HEALTHY


def _status_title(status: str) -> str:
    if status == STARTUP_STATUS_STORAGE_MISMATCH:
        return "Storage Mismatch Suspected"
    if status == STARTUP_STATUS_FRESH_INSTALL:
        return "Fresh Install State"
    if status == STARTUP_STATUS_EMPTY_DATABASE:
        return "Empty Database"
    return "Healthy"


def _status_summary(status: str, comics_root: str) -> str:
    if status == STARTUP_STATUS_STORAGE_MISMATCH:
        return (
            f"Parker can see entries under {comics_root}, but the active database has no libraries configured. "
            "This usually means the server started against a different or newly initialized /app/storage directory."
        )
    if status == STARTUP_STATUS_FRESH_INSTALL:
        return (
            "Parker appears to be running with the default admin account and an empty database. "
            "This is expected on a brand new install."
        )
    if status == STARTUP_STATUS_EMPTY_DATABASE:
        return (
            "Parker is running with an empty database, but the usual default-admin fingerprint was not detected."
        )
    return "Parker found existing content or configuration in the active database."


def _build_recommended_actions(status: str) -> list[str]:
    if status == STARTUP_STATUS_STORAGE_MISMATCH:
        return [
            "Verify that /app/storage points to the same host folder or volume used before the upgrade.",
            "Compare the active comics.db file with the previous deployment and confirm the expected libraries exist there.",
            "If this was an upgrade, avoid adding new libraries until the original storage path has been verified.",
        ]
    if status in {STARTUP_STATUS_FRESH_INSTALL, STARTUP_STATUS_EMPTY_DATABASE}:
        return [
            "If this is a brand new server, continue with normal setup.",
            "If this is unexpected, verify the /app/storage bind mount or Docker volume before making changes.",
        ]
    return []


def collect_startup_diagnostics(
    db: Session,
    *,
    database_url: str,
    comics_root: Path = Path("/comics"),
) -> dict:
    db_path = resolve_sqlite_db_path(database_url)
    db_exists = bool(db_path and db_path.exists())
    db_size = _safe_file_size(db_path)
    wal_size = _safe_file_size(Path(f"{db_path}-wal")) if db_path else None
    shm_size = _safe_file_size(Path(f"{db_path}-shm")) if db_path else None

    users_count = db.query(User).count()
    libraries_count = db.query(Library).count()
    series_count = db.query(Series).count()
    comics_count = db.query(Comic).count()

    default_admin_present = db.query(User).filter(
        User.username == "admin",
        User.email == "admin@example.com",
        User.is_superuser == True,
    ).first() is not None

    library_sample = [
        {"name": library.name, "path": library.path}
        for library in db.query(Library).order_by(Library.name).limit(5).all()
    ]

    comics_root_exists = comics_root.exists()
    comics_root_sample = _sample_directory(comics_root)
    alembic_version = _safe_alembic_version(db)
    runtime_mode = _detect_runtime_mode(
        comics_root,
        comics_root_exists=comics_root_exists,
        library_sample=library_sample,
    )

    status = _classify_startup_status(
        users_count=users_count,
        libraries_count=libraries_count,
        series_count=series_count,
        comics_count=comics_count,
        default_admin_present=default_admin_present,
        comics_root_sample=comics_root_sample,
    )

    return {
        "status": status,
        "status_title": _status_title(status),
        "status_summary": _status_summary(status, str(comics_root)),
        "is_suspicious": status == STARTUP_STATUS_STORAGE_MISMATCH,
        "database": {
            "url": database_url,
            "path": str(db_path.resolve(strict=False)) if db_path else None,
            "exists": db_exists,
            "size_bytes": db_size,
            "wal_size_bytes": wal_size,
            "shm_size_bytes": shm_size,
            "alembic_version": alembic_version,
        },
        "counts": {
            "users": users_count,
            "libraries": libraries_count,
            "series": series_count,
            "comics": comics_count,
        },
        "default_admin_present": default_admin_present,
        "library_sample": library_sample,
        "runtime": {
            "mode": runtime_mode,
            "label": "Container-like" if runtime_mode == RUNTIME_MODE_CONTAINER else "Local filesystem",
        },
        "comics_root": {
            "path": str(comics_root),
            "exists": comics_root_exists,
            "sample": comics_root_sample,
        },
        "recommended_actions": _build_recommended_actions(status),
    }


def build_home_startup_notice(diagnostics: dict, *, is_admin: bool) -> dict | None:
    status = diagnostics.get("status")
    if status == STARTUP_STATUS_HEALTHY:
        return None

    if status == STARTUP_STATUS_STORAGE_MISMATCH:
        return {
            "status": status,
            "title": diagnostics["status_title"],
            "summary": diagnostics["status_summary"],
            "is_suspicious": True,
            "is_admin": is_admin,
            "recommended_actions": diagnostics["recommended_actions"],
            "diagnostics_url": "/admin/diagnostics" if is_admin else None,
        }

    return None


def build_support_snapshot(
    diagnostics: dict,
    *,
    app_version: str,
    generated_at: datetime | None = None,
) -> dict:
    timestamp = generated_at or datetime.now(timezone.utc)

    return {
        "snapshot_type": "parker_startup_diagnostics",
        "schema_version": 1,
        "generated_at_utc": timestamp.isoformat(),
        "app_version": app_version,
        "status": {
            "code": diagnostics["status"],
            "title": diagnostics["status_title"],
            "summary": diagnostics["status_summary"],
            "is_suspicious": diagnostics["is_suspicious"],
        },
        "runtime": diagnostics["runtime"],
        "database": diagnostics["database"],
        "counts": diagnostics["counts"],
        "default_admin_present": diagnostics["default_admin_present"],
        "configured_library_sample": diagnostics["library_sample"],
        "comics_probe": diagnostics["comics_root"],
        "recommended_actions": diagnostics["recommended_actions"],
    }


def log_startup_diagnostics(
    db: Session,
    *,
    database_url: str,
    comics_root: Path = Path("/comics"),
) -> None:
    diagnostics = collect_startup_diagnostics(
        db,
        database_url=database_url,
        comics_root=comics_root,
    )

    database = diagnostics["database"]
    counts = diagnostics["counts"]
    comics_root_info = diagnostics["comics_root"]

    logger.info(
        "Startup storage diagnostic: database_url=%s db_path=%s exists=%s size_bytes=%s wal_size_bytes=%s shm_size_bytes=%s alembic_version=%s",
        database["url"],
        database["path"],
        database["exists"],
        database["size_bytes"],
        database["wal_size_bytes"],
        database["shm_size_bytes"],
        database["alembic_version"],
    )
    logger.info(
        "Startup storage diagnostic: status=%s counts users=%s libraries=%s series=%s comics=%s default_admin_present=%s library_sample=%s comics_root=%s comics_root_exists=%s comics_root_sample=%s",
        diagnostics["status"],
        counts["users"],
        counts["libraries"],
        counts["series"],
        counts["comics"],
        diagnostics["default_admin_present"],
        diagnostics["library_sample"],
        comics_root_info["path"],
        comics_root_info["exists"],
        comics_root_info["sample"],
    )

    if diagnostics["status"] in {STARTUP_STATUS_FRESH_INSTALL, STARTUP_STATUS_EMPTY_DATABASE}:
        logger.warning(
            "Startup storage diagnostic: %s",
            diagnostics["status_summary"],
        )

    if diagnostics["status"] == STARTUP_STATUS_STORAGE_MISMATCH:
        logger.warning(
            "Startup storage diagnostic: %s",
            diagnostics["status_summary"],
        )
