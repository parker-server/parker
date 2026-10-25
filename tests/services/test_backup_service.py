import logging
import os
import shutil
import sqlite3
import tarfile
import time
from types import SimpleNamespace

import pytest

import app.services.backup as backup_module
from app.services.backup import BackupService


def _create_sqlite_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE comics (id INTEGER PRIMARY KEY, title TEXT)")
        connection.execute("INSERT INTO comics (title) VALUES (?)", ("Backup Canary",))
        connection.commit()
    finally:
        connection.close()


def _sqlite_url(path):
    return f"sqlite:///{path.as_posix()}"


def _create_wal_db(path, rows=200, delete_rows=True):
    """Create a WAL-mode database; deleting rows leaves most of the file unused."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE comics (id INTEGER PRIMARY KEY, title TEXT, metadata_json TEXT)")
        connection.execute("INSERT INTO comics (title, metadata_json) VALUES ('Compact Canary', '{}')")
        connection.executemany(
            "INSERT INTO comics (title, metadata_json) VALUES (?, ?)",
            [(f"Filler {index}", "x" * 20_000) for index in range(rows)],
        )
        if delete_rows:
            connection.execute("DELETE FROM comics WHERE title LIKE 'Filler %'")
        connection.commit()
    finally:
        connection.close()


def _read_canary(path):
    connection = sqlite3.connect(path)
    try:
        title = connection.execute("SELECT title FROM comics").fetchone()[0]
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        connection.close()
    return title, journal_mode


@pytest.fixture
def compact_db(tmp_path, monkeypatch):
    db_path = tmp_path / "database" / "comics.db"
    monkeypatch.setattr(backup_module.settings, "database_url", _sqlite_url(db_path))
    # Test databases are a few MB, so drop the 50 MB floor unless a test restores it.
    monkeypatch.setattr(backup_module, "COMPACT_MIN_FREE_BYTES", 0)
    return db_path


def test_create_backup_archives_database_and_removes_temp_copy(tmp_path, monkeypatch):
    db_path = tmp_path / "database" / "comics.db"
    backup_dir = tmp_path / "backups"
    _create_sqlite_db(db_path)
    monkeypatch.setattr(backup_module.settings, "database_url", _sqlite_url(db_path))
    monkeypatch.setattr(backup_module.settings, "backup_dir", backup_dir)
    monkeypatch.setattr(backup_module, "get_system_setting", lambda key, default=None: 0)

    result = BackupService.create_backup()

    archive_path = backup_dir / result["filename"]
    assert result["path"] == str(archive_path)
    assert result["size_bytes"] == archive_path.stat().st_size
    assert result["filename"] == f"comics_backup_{result['timestamp']}.tar.gz"
    assert archive_path.exists()
    assert list(backup_dir.glob("*.db")) == []

    restored_db_path = tmp_path / "restored.db"
    expected_member_name = f"comics_backup_{result['timestamp']}.db"
    with tarfile.open(archive_path, "r:gz") as archive:
        assert archive.getnames() == [expected_member_name]
        member = archive.extractfile(expected_member_name)
        assert member is not None
        restored_db_path.write_bytes(member.read())

    connection = sqlite3.connect(restored_db_path)
    try:
        title = connection.execute("SELECT title FROM comics").fetchone()[0]
    finally:
        connection.close()
    assert title == "Backup Canary"


def test_create_backup_raises_when_database_file_is_missing(tmp_path, monkeypatch):
    missing_db_path = tmp_path / "missing.db"
    monkeypatch.setattr(backup_module.settings, "database_url", _sqlite_url(missing_db_path))
    monkeypatch.setattr(backup_module.settings, "backup_dir", tmp_path / "backups")

    with pytest.raises(FileNotFoundError, match="Database not found"):
        BackupService.create_backup()


def test_cleanup_old_backups_deletes_only_expired_backup_files(tmp_path, monkeypatch):
    old_backup = tmp_path / "comics_backup_old.tar.gz"
    recent_backup = tmp_path / "comics_backup_recent.tar.gz"
    unrelated_archive = tmp_path / "other_backup_old.tar.gz"
    for file in [old_backup, recent_backup, unrelated_archive]:
        file.write_text("backup", encoding="utf-8")

    now = time.time()
    os.utime(old_backup, (now - (3 * 86400), now - (3 * 86400)))
    os.utime(unrelated_archive, (now - (3 * 86400), now - (3 * 86400)))
    monkeypatch.setattr(backup_module, "get_system_setting", lambda key, default=None: 1)

    BackupService.cleanup_old_backups(tmp_path)

    assert not old_backup.exists()
    assert recent_backup.exists()
    assert unrelated_archive.exists()


def test_cleanup_old_backups_keeps_files_when_retention_is_disabled(tmp_path, monkeypatch):
    old_backup = tmp_path / "comics_backup_keep.tar.gz"
    old_backup.write_text("backup", encoding="utf-8")
    old_time = time.time() - (30 * 86400)
    os.utime(old_backup, (old_time, old_time))
    monkeypatch.setattr(backup_module, "get_system_setting", lambda key, default=None: 0)

    BackupService.cleanup_old_backups(tmp_path)

    assert old_backup.exists()


def test_cleanup_old_backups_logs_delete_failures(tmp_path, monkeypatch, caplog):
    old_backup = tmp_path / "comics_backup_locked.tar.gz"
    old_backup.write_text("backup", encoding="utf-8")
    old_time = time.time() - (3 * 86400)
    os.utime(old_backup, (old_time, old_time))
    monkeypatch.setattr(backup_module, "get_system_setting", lambda key, default=None: 1)

    def fail_remove(path):
        raise OSError("file is locked")

    monkeypatch.setattr(backup_module.os, "remove", fail_remove)

    with caplog.at_level(logging.ERROR, logger="app.services.backup"):
        BackupService.cleanup_old_backups(tmp_path)

    assert old_backup.exists()
    assert "Failed to delete comics_backup_locked.tar.gz: file is locked" in caplog.text


def test_compact_database_if_needed_shrinks_file_with_lots_of_unused_space(compact_db, caplog):
    _create_wal_db(compact_db)
    size_before = compact_db.stat().st_size

    with caplog.at_level(logging.INFO, logger="app.services.backup"):
        result = BackupService.compact_database_if_needed()

    assert result["status"] == "compacted"
    assert result["size_before"] == size_before
    assert result["size_after"] == compact_db.stat().st_size
    assert compact_db.stat().st_size < size_before / 4
    assert _read_canary(compact_db) == ("Compact Canary", "wal")
    assert "Database compacted" in caplog.text


def test_compact_database_if_needed_skips_when_little_space_is_unused(compact_db):
    _create_wal_db(compact_db, delete_rows=False)
    size_before = compact_db.stat().st_size

    assert BackupService.compact_database_if_needed() == {"status": "not_needed"}
    assert compact_db.stat().st_size == size_before


def test_compact_database_if_needed_skips_below_minimum_unused_bytes(compact_db, monkeypatch):
    _create_wal_db(compact_db)
    size_before = compact_db.stat().st_size
    monkeypatch.setattr(backup_module, "COMPACT_MIN_FREE_BYTES", 50 * 1024 * 1024)

    assert BackupService.compact_database_if_needed() == {"status": "not_needed"}
    assert compact_db.stat().st_size == size_before


def test_compact_database_if_needed_retries_after_low_disk_space(compact_db, monkeypatch, caplog):
    _create_wal_db(compact_db)
    size_before = compact_db.stat().st_size

    with monkeypatch.context() as low_disk:
        low_disk.setattr(shutil, "disk_usage", lambda _path: SimpleNamespace(free=0))
        with caplog.at_level(logging.WARNING, logger="app.services.backup"):
            assert BackupService.compact_database_if_needed() == {"status": "skipped"}

    assert compact_db.stat().st_size == size_before
    assert "Skipped compacting the database: needs about" in caplog.text
    assert "It will try again on the next start." in caplog.text

    # Next start, with room on disk: the unused space is still there, so it compacts.
    assert BackupService.compact_database_if_needed()["status"] == "compacted"
    assert compact_db.stat().st_size < size_before / 4


def test_compact_database_if_needed_warns_instead_of_raising_when_vacuum_fails(compact_db, monkeypatch, caplog):
    _create_wal_db(compact_db)
    real_connect = sqlite3.connect

    class VacuumFailsConnection:
        def __init__(self, connection):
            self._connection = connection

        def execute(self, sql, *args):
            if sql == "VACUUM":
                raise sqlite3.OperationalError("database or disk is full")
            return self._connection.execute(sql, *args)

        def close(self):
            self._connection.close()

    monkeypatch.setattr(backup_module.sqlite3, "connect", lambda *args, **kwargs: VacuumFailsConnection(real_connect(*args, **kwargs)))

    with caplog.at_level(logging.WARNING, logger="app.services.backup"):
        result = BackupService.compact_database_if_needed()

    assert result == {"status": "failed"}
    assert "Skipped compacting the database: database or disk is full" in caplog.text
    assert _read_canary(compact_db) == ("Compact Canary", "wal")


def test_compact_database_if_needed_ignores_missing_database_file(compact_db):
    assert BackupService.compact_database_if_needed() == {"status": "not_needed"}
    assert not compact_db.exists()
