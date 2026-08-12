import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import desc, text
from sqlalchemy.orm import Session, selectinload

from app.core.security import verify_password
from app.models.comic import Comic
from app.models.job import ScanJob
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
RECENT_JOB_LIMIT = 3
SAFE_JOB_SUMMARY_KEYS = {
    "characters",
    "collections_restored",
    "comics_scanned",
    "deleted",
    "elapsed",
    "empty_collections",
    "empty_lists",
    "errors",
    "force_scan_recommended",
    "imported",
    "locations",
    "missing_files_removed",
    "old_jobs",
    "orphaned_thumbnails_deleted",
    "people",
    "processed",
    "reading_lists_restored",
    "series",
    "skipped",
    "source_metadata_invalid",
    "source_metadata_missing",
    "story_arcs_restored",
    "teams",
    "updated",
    "volumes",
}


def resolve_sqlite_db_path(database_url: str) -> Path | None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return None

    raw_path = database_url[len(prefix):]
    return Path(raw_path)


def _path_tail(path_str: str) -> str:
    normalized = path_str.replace("\\", "/").rstrip("/")
    if not normalized:
        return ""

    return normalized.rsplit("/", 1)[-1] or normalized


def _redact_path(path_str: str | None) -> str | None:
    if not path_str:
        return path_str

    normalized = str(path_str).replace("\\", "/").rstrip("/")
    if not normalized:
        return normalized

    if normalized in {"/", ".", ".."}:
        return normalized

    if normalized.lower() == ":memory:":
        return normalized

    tail = _path_tail(normalized)
    if not tail:
        return normalized

    if len(normalized) >= 2 and normalized[1] == ":":
        return f"{normalized[:2]}/.../{tail}"

    if normalized.startswith("//"):
        parts = [part for part in normalized.split("/") if part]
        if len(parts) >= 2:
            return f"//{parts[0]}/.../{tail}"
        return f"//.../{tail}"

    if normalized.startswith("/"):
        parts = [part for part in normalized.split("/") if part]
        if len(parts) <= 1:
            return normalized
        return f"/.../{tail}"

    if "/" not in normalized:
        return normalized

    return f".../{tail}"


def _redact_database_url(database_url: str) -> str:
    sqlite_prefix = "sqlite:///"
    if database_url.startswith(sqlite_prefix):
        raw_path = database_url[len(sqlite_prefix):]
        if raw_path == ":memory:":
            return "sqlite:///:memory:"
        redacted_path = _redact_path(raw_path) or raw_path
        return f"sqlite:///{redacted_path.lstrip('/')}"

    parsed = urlsplit(database_url)
    if not parsed.scheme:
        return _redact_path(database_url) or database_url

    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    netloc = f"<credentials>@{host}" if parsed.username or parsed.password else host
    path_tail = _path_tail(parsed.path)
    redacted_path = f"/.../{path_tail}" if path_tail else ""

    return urlunsplit((parsed.scheme, netloc, redacted_path, "", ""))


def _safe_file_size(path: Path | None) -> int | None:
    if path is None or not path.exists():
        return None

    try:
        return path.stat().st_size
    except OSError:
        return None


def _safe_path_exists(path_str: str | None) -> bool | None:
    if not path_str:
        return None

    try:
        return Path(path_str).exists()
    except (OSError, ValueError):
        return None


def _format_bytes(size_bytes: int | None) -> str | None:
    if size_bytes is None:
        return None

    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size_bytes)
    unit_index = 0

    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"

    return f"{value:.1f} {units[unit_index]}"


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


def _isoformat_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None

    return value.isoformat()


def _duration_seconds(started_at: datetime | None, completed_at: datetime | None) -> float | None:
    if not started_at or not completed_at:
        return None

    return round((completed_at - started_at).total_seconds(), 3)


def _safe_job_type(value: object) -> str:
    return str(getattr(value, "value", value))


def _safe_job_summary(raw_summary: str | None) -> dict:
    if not raw_summary:
        return {}

    try:
        parsed = json.loads(raw_summary)
    except (TypeError, ValueError):
        return {}

    if not isinstance(parsed, dict):
        return {}

    safe_summary = {}
    for key, value in parsed.items():
        if key not in SAFE_JOB_SUMMARY_KEYS:
            continue
        if isinstance(value, bool) or isinstance(value, int) or isinstance(value, float):
            safe_summary[key] = value

    return safe_summary


def _recent_job_summaries(db: Session, limit: int = RECENT_JOB_LIMIT) -> list[dict]:
    jobs = (
        db.query(ScanJob)
        .order_by(desc(ScanJob.created_at), desc(ScanJob.id))
        .limit(limit)
        .all()
    )

    return [
        {
            "job_type": _safe_job_type(job.job_type),
            "status": _safe_job_type(job.status),
            "scope": "global" if job.library_id is None else "library",
            "force_scan": bool(job.force_scan),
            "created_at": _isoformat_datetime(job.created_at),
            "started_at": _isoformat_datetime(job.started_at),
            "completed_at": _isoformat_datetime(job.completed_at),
            "duration_seconds": _duration_seconds(job.started_at, job.completed_at),
            "summary": _safe_job_summary(job.result_summary),
            "has_error": bool(job.error_message),
        }
        for job in jobs
    ]


def _legacy_default_admin_password_active(db: Session) -> bool:
    user = db.query(User).filter(
        User.username == "admin",
        User.is_superuser == True,
        User.is_active == True,
    ).first()
    if user is None:
        return False

    try:
        return verify_password("admin", user.hashed_password)
    except Exception:
        return False


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

    library_paths = []
    for item in library_sample:
        if item.get("path"):
            library_paths.append(item["path"])
        library_paths.extend(root.get("path", "") for root in item.get("roots", []))

    if any(_looks_like_container_library_path(path) for path in library_paths):
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
            "This usually means the server started against a different or newly initialized storage directory."
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
            "Verify that Parker is using the same host folder or volume it used before the upgrade.",
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
    include_security_checks: bool = False,
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
    legacy_default_admin_password_active = (
        _legacy_default_admin_password_active(db)
        if include_security_checks
        else False
    )

    library_sample = []
    for library in (
        db.query(Library)
        .options(selectinload(Library.roots))
        .order_by(Library.name)
        .limit(5)
        .all()
    ):
        active_root = library.active_root
        root_path = active_root.path if active_root else None
        roots = [
            {
                "id": root.id,
                "path": root.path,
                "path_display": _redact_path(root.path),
                "is_active": root.is_active,
                "path_exists": _safe_path_exists(root.path),
            }
            for root in sorted(library.roots, key=lambda row: row.id)
        ]
        library_sample.append({
            "name": library.name,
            "path": root_path,
            "path_display": _redact_path(root_path),
            "path_exists": _safe_path_exists(root_path),
            "root_count": len(roots),
            "active_root_count": sum(1 for root in roots if root["is_active"]),
            "roots": roots,
        })

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
    db_path_str = str(db_path.resolve(strict=False)) if db_path else None
    comics_root_display = _redact_path(str(comics_root)) or str(comics_root)

    return {
        "status": status,
        "status_title": _status_title(status),
        "status_summary": _status_summary(status, comics_root_display),
        "is_suspicious": status == STARTUP_STATUS_STORAGE_MISMATCH,
        "database": {
            "url": database_url,
            "url_display": _redact_database_url(database_url),
            "path": db_path_str,
            "path_display": _redact_path(db_path_str),
            "exists": db_exists,
            "size_bytes": db_size,
            "size_display": _format_bytes(db_size),
            "wal_size_bytes": wal_size,
            "wal_size_display": _format_bytes(wal_size),
            "shm_size_bytes": shm_size,
            "shm_size_display": _format_bytes(shm_size),
            "alembic_version": alembic_version,
        },
        "counts": {
            "users": users_count,
            "libraries": libraries_count,
            "series": series_count,
            "comics": comics_count,
        },
        "default_admin_present": default_admin_present,
        "legacy_default_admin_password_active": legacy_default_admin_password_active,
        "library_sample": library_sample,
        "recent_jobs": _recent_job_summaries(db),
        "runtime": {
            "mode": runtime_mode,
            "label": "Container-like" if runtime_mode == RUNTIME_MODE_CONTAINER else "Local filesystem",
        },
        "comics_root": {
            "path": str(comics_root),
            "path_display": _redact_path(str(comics_root)),
            "exists": comics_root_exists,
            "sample": comics_root_sample,
            "sample_count": len(comics_root_sample),
        },
        "recommended_actions": _build_recommended_actions(status),
    }


def build_home_startup_notice(diagnostics: dict, *, is_admin: bool) -> dict | None:
    status = diagnostics.get("status")
    if status == STARTUP_STATUS_HEALTHY:
        if diagnostics.get("legacy_default_admin_password_active") and is_admin:
            return _build_legacy_default_admin_password_notice()
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
            "primary_action_url": "/admin/diagnostics" if is_admin else None,
            "primary_action_title": "Open Diagnostics",
            "primary_action_summary": "Inspect the active database path, counts, configured libraries, and comics probe before making any changes.",
        }

    if diagnostics.get("legacy_default_admin_password_active") and is_admin:
        return _build_legacy_default_admin_password_notice()

    return None


def _build_legacy_default_admin_password_notice() -> dict:
    return {
        "status": "legacy_default_admin_password_active",
        "title": "Legacy Default Admin Password",
        "summary": (
            "The built-in admin account still accepts Parker's legacy default password. "
            "Change it before exposing Parker outside a trusted network."
        ),
        "is_suspicious": True,
        "is_admin": True,
        "recommended_actions": [
            "Open Account Settings and change the admin password.",
            "Use a unique password that is not shared with other services.",
        ],
        "primary_action_url": "/user/settings",
        "primary_action_title": "Update Password",
        "primary_action_summary": "Change the current admin password from your account settings.",
    }


def _redacted_database_snapshot(database: dict) -> dict:
    return {
        "url": database.get("url_display") or _redact_database_url(database.get("url") or ""),
        "path": database.get("path_display") or _redact_path(database.get("path")),
        "exists": database.get("exists"),
        "size_bytes": database.get("size_bytes"),
        "size_display": database.get("size_display"),
        "wal_size_bytes": database.get("wal_size_bytes"),
        "wal_size_display": database.get("wal_size_display"),
        "shm_size_bytes": database.get("shm_size_bytes"),
        "shm_size_display": database.get("shm_size_display"),
        "alembic_version": database.get("alembic_version"),
        "paths_redacted": True,
    }


def _redacted_library_snapshot(library_sample: list[dict]) -> list[dict]:
    redacted_libraries = []
    for library in library_sample:
        roots = [
            {
                "path": root.get("path_display") or _redact_path(root.get("path")),
                "is_active": root.get("is_active"),
                "path_exists": root.get("path_exists"),
            }
            for root in library.get("roots", [])
        ]
        redacted_libraries.append({
            "name": library.get("name"),
            "path": library.get("path_display") or _redact_path(library.get("path")),
            "path_exists": library.get("path_exists"),
            "root_count": library.get("root_count"),
            "active_root_count": library.get("active_root_count"),
            "roots": roots,
            "paths_redacted": True,
        })

    return redacted_libraries


def _redacted_comics_probe_snapshot(comics_root: dict) -> dict:
    sample = comics_root.get("sample") or []
    return {
        "path": comics_root.get("path_display") or _redact_path(comics_root.get("path")),
        "exists": comics_root.get("exists"),
        "sample_count": comics_root.get("sample_count", len(sample)),
        "sample_names_redacted": bool(sample),
        "paths_redacted": True,
    }


def build_support_snapshot(
    diagnostics: dict,
    *,
    app_version: str,
    git_commit_hash: str | None = None,
    generated_at: datetime | None = None,
) -> dict:
    timestamp = generated_at or datetime.now(timezone.utc)

    return {
        "snapshot_type": "parker_startup_diagnostics",
        "schema_version": 1,
        "generated_at_utc": timestamp.isoformat(),
        "app_version": app_version,
        "build": {
            "app_version": app_version,
            "git_commit_hash": git_commit_hash,
        },
        "status": {
            "code": diagnostics["status"],
            "title": diagnostics["status_title"],
            "summary": diagnostics["status_summary"],
            "is_suspicious": diagnostics["is_suspicious"],
        },
        "runtime": diagnostics["runtime"],
        "database": _redacted_database_snapshot(diagnostics["database"]),
        "counts": diagnostics["counts"],
        "default_admin_present": diagnostics["default_admin_present"],
        "configured_library_sample": _redacted_library_snapshot(diagnostics["library_sample"]),
        "recent_jobs": diagnostics.get("recent_jobs", []),
        "comics_probe": _redacted_comics_probe_snapshot(diagnostics["comics_root"]),
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
