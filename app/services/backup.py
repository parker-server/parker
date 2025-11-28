import sqlite3
import tarfile
import os
from datetime import datetime
from pathlib import Path
from app.config import settings


class BackupService:
    def create_backup(self) -> dict:
        """
        Perform a hot backup of the SQLite database and compress it.
        """
        # 1. Determine Paths
        # Parse "sqlite:///./storage/..." to get the real path
        db_path = settings.database_url.replace("sqlite:///", "")

        if not Path(db_path).exists():
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

        return {
            "filename": archive_path.name,
            "path": str(archive_path),
            "size_bytes": archive_path.stat().st_size,
            "timestamp": timestamp
        }