import logging
import sqlite3
import tarfile
import os
import time
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.core.settings_loader import get_system_setting

logger = logging.getLogger(__name__)

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