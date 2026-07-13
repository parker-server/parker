import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.comic import Comic
from app.models.library import Library
from app.models.series import Series
from app.models.user import User


logger = logging.getLogger("app.startup")


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


def log_startup_diagnostics(
    db: Session,
    *,
    database_url: str,
    comics_root: Path = Path("/comics"),
) -> None:
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

    logger.info(
        "Startup storage diagnostic: database_url=%s db_path=%s exists=%s size_bytes=%s wal_size_bytes=%s shm_size_bytes=%s alembic_version=%s",
        database_url,
        str(db_path.resolve(strict=False)) if db_path else None,
        db_exists,
        db_size,
        wal_size,
        shm_size,
        alembic_version,
    )
    logger.info(
        "Startup storage diagnostic: counts users=%s libraries=%s series=%s comics=%s default_admin_present=%s library_sample=%s comics_root=%s comics_root_exists=%s comics_root_sample=%s",
        users_count,
        libraries_count,
        series_count,
        comics_count,
        default_admin_present,
        library_sample,
        str(comics_root),
        comics_root_exists,
        comics_root_sample,
    )

    if (
        libraries_count == 0
        and series_count == 0
        and comics_count == 0
        and default_admin_present
    ):
        logger.warning(
            "Startup storage diagnostic: Parker is running with an effectively empty database. If this is unexpected after an upgrade, verify that /app/storage points to the same host folder or volume as before."
        )

    if libraries_count == 0 and comics_root_sample:
        logger.warning(
            "Startup storage diagnostic: the comics mount at %s has visible top-level entries %s, but the database has no libraries configured. Parker does not auto-create libraries from the comics mount, so this often indicates a fresh or different /app/storage directory.",
            str(comics_root),
            comics_root_sample,
        )
