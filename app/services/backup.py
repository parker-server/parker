import logging
import shutil
import sqlite3
import tarfile
import tempfile
import os
import time
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.core.settings_loader import get_system_setting
from app.services.startup_diagnostics import resolve_sqlite_db_path

logger = logging.getLogger(__name__)

# VACUUM rewrites the whole file, so only compact when enough of it is unused.
COMPACT_MIN_FREE_RATIO = 0.25
COMPACT_MIN_FREE_BYTES = 50 * 1024 * 1024
UNCOMPACTED_NOTE = "Parker works normally; SQLite reuses the unused space for new data."


class BackupService:

    @staticmethod
    def create_backup() -> dict:

        """
        Perform a hot backup of the SQLite database and compress it.
        """
        # 1. Determine Paths
        # Parse "sqlite:///./storage/..." to get the real path
        db_path = settings.database_url.replace("sqlite:///", "")

        if not Path(db_path).exists():
            logger.error(f"Database path does not exist: {db_path}")
            raise FileNotFoundError(f"Database not found at {db_path}")

        backup_dir = settings.backup_dir
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename_base = f"comics_backup_{timestamp}"

        temp_db_path = backup_dir / f"{filename_base}.db"
        archive_path = backup_dir / f"{filename_base}.tar.gz"

        # 2. Perform Hot Backup (Safe for WAL mode)
        # We connect directly to the file to perform the low-level backup
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(str(temp_db_path))

        try:
            with dst:
                # Copies the database pages safely
                src.backup(dst)
        finally:
            dst.close()
            src.close()

        # 3. Compress to Tar/Gzip
        try:
            with tarfile.open(archive_path, "w:gz") as tar:
                # Add the file to the archive with a clean name (no folders)
                tar.add(temp_db_path, arcname=f"{filename_base}.db")
        finally:
            # 4. Cleanup the raw .db copy
            if temp_db_path.exists():
                os.remove(temp_db_path)

        # 4. Enforce Retention Policy
        BackupService.cleanup_old_backups(backup_dir)

        return {
            "filename": archive_path.name,
            "path": str(archive_path),
            "size_bytes": archive_path.stat().st_size,
            "timestamp": timestamp
        }

    @staticmethod
    def cleanup_old_backups(backup_dir: Path):
        """
        Delete backups older than the configured retention days.
        """
        retention_days = 7  # Default

        # Use the loader helper to fetch safely without manual session management
        retention_days = get_system_setting("backup.retention_days", 7)

        # If 0, assume "Keep Forever"
        if retention_days <= 0:
            return

        logger.info(f"Running backup cleanup (Retention: {retention_days} days)")

        # Calculate cutoff time (in seconds)
        cutoff_time = time.time() - (retention_days * 86400)

        count = 0
        for file in backup_dir.glob("comics_backup_*.tar.gz"):
            try:
                # Check file modification time
                if file.stat().st_mtime < cutoff_time:
                    os.remove(file)
                    count += 1
                    logger.info(f"Deleted old backup: {file.name}")
            except Exception as e:
                logger.error(f"Failed to delete {file.name}: {e}")

        if count > 0:
            logger.info(f"Cleaned up {count} old backup(s).")

    @staticmethod
    def compact_database_if_needed() -> dict:
        """
        Shrink the database file (SQLite VACUUM) when a large share of it is unused,
        for example after a migration or cleanup that removed a lot of data.

        Runs at container start, before the server takes requests. Compaction is
        optional, so a skip or failure only logs a warning and never raises.
        It retries on every start until it succeeds, then stops triggering.
        """
        db_path = resolve_sqlite_db_path(settings.database_url)
        if db_path is None or not db_path.exists():
            return {"status": "not_needed"}

        try:
            # Autocommit mode: VACUUM can't run inside a transaction.
            connection = sqlite3.connect(db_path, isolation_level=None)
            try:
                page_size = connection.execute("PRAGMA page_size").fetchone()[0]
                page_count = connection.execute("PRAGMA page_count").fetchone()[0]
                free_pages = connection.execute("PRAGMA freelist_count").fetchone()[0]
                free_bytes = free_pages * page_size
                if free_bytes < COMPACT_MIN_FREE_BYTES or free_pages < page_count * COMPACT_MIN_FREE_RATIO:
                    return {"status": "not_needed"}

                if not BackupService._has_room_to_vacuum(db_path, (page_count - free_pages) * page_size):
                    return {"status": "skipped"}

                size_before = db_path.stat().st_size
                logger.info(
                    "Compacting database: %.0f MB of %.0f MB is unused...",
                    free_bytes / 1e6,
                    page_count * page_size / 1e6,
                )
                connection.execute("VACUUM")
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                connection.close()
        except Exception as exc:
            logger.warning(
                "Skipped compacting the database: %s. It will try again on the next start. %s",
                exc,
                UNCOMPACTED_NOTE,
            )
            return {"status": "failed"}

        size_after = db_path.stat().st_size
        logger.info("Database compacted: %.1f MB -> %.1f MB", size_before / 1e6, size_after / 1e6)
        return {"status": "compacted", "size_before": size_before, "size_after": size_after}

    @staticmethod
    def _has_room_to_vacuum(db_path: Path, used_bytes: int) -> bool:
        # VACUUM writes a compacted copy to the temp folder, then writes it back
        # through the WAL file next to the database. Each is about the used size.
        checks = [
            (db_path.parent, 2 * used_bytes),
            (Path(tempfile.gettempdir()), used_bytes),
        ]
        for folder, needed in checks:
            free = shutil.disk_usage(folder).free
            if free < needed:
                logger.warning(
                    "Skipped compacting the database: needs about %.0f MB free in %s, but only %.0f MB is free. "
                    "It will try again on the next start. %s",
                    needed / 1e6,
                    folder,
                    free / 1e6,
                    UNCOMPACTED_NOTE,
                )
                return False
        return True