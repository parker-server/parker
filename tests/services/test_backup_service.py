import logging
import os
import sqlite3
import tarfile
import time

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
