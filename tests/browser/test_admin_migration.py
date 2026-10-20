import sqlite3

import pytest

from app.models.user import User


def _create_kavita_preview_db(path) -> None:
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE AspNetUsers (Id INTEGER PRIMARY KEY, UserName TEXT, Email TEXT);
        CREATE TABLE AspNetUserRoles (UserId INTEGER, RoleId INTEGER);
        CREATE TABLE AspNetRoles (Id INTEGER PRIMARY KEY, Name TEXT, NormalizedName TEXT);
        CREATE TABLE AppUserLibrary (AppUsersId INTEGER, LibrariesId INTEGER);
        CREATE TABLE Library (Id INTEGER PRIMARY KEY, Name TEXT);
        CREATE TABLE AppUserProgresses (
            Id INTEGER PRIMARY KEY,
            AppUserId INTEGER,
            ChapterId INTEGER,
            PagesRead INTEGER,
            LastModified TEXT,
            LastModifiedUtc TEXT
        );
        CREATE TABLE Series (Id INTEGER PRIMARY KEY, Name TEXT);
        CREATE TABLE Volume (Id INTEGER PRIMARY KEY, SeriesId INTEGER, Name TEXT, Number INTEGER);
        CREATE TABLE Chapter (Id INTEGER PRIMARY KEY, VolumeId INTEGER, Number TEXT, IsSpecial INTEGER);
        CREATE TABLE MangaFile (Id INTEGER PRIMARY KEY, ChapterId INTEGER, FilePath TEXT);
        """
    )
    cur.executemany(
        "INSERT INTO AspNetUsers (Id, UserName, Email) VALUES (?, ?, ?)",
        [
            (1, "browser-user", "browser@example.com"),
            (2, "preview-new-user", "preview-new-user@example.com"),
        ],
    )
    cur.execute("INSERT INTO Series (Id, Name) VALUES (?, ?)", (1, "Smoke Series"))
    cur.execute("INSERT INTO Volume (Id, SeriesId, Name, Number) VALUES (?, ?, ?, ?)", (1, 1, "1", 1))
    cur.execute("INSERT INTO Chapter (Id, VolumeId, Number, IsSpecial) VALUES (?, ?, ?, ?)", (1, 1, "1", 0))
    cur.execute(
        "INSERT INTO MangaFile (Id, ChapterId, FilePath) VALUES (?, ?, ?)",
        (1, 1, "/tmp/browser-test-library/smoke-reader.cbz"),
    )
    cur.executemany(
        """
        INSERT INTO AppUserProgresses (AppUserId, ChapterId, PagesRead, LastModified, LastModifiedUtc)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (1, 1, 2, "2026-01-01 10:00:00", "2026-01-01 14:00:00"),
            (2, 1, 3, "2026-01-02 10:00:00", "2026-01-02 14:00:00"),
        ],
    )
    conn.commit()
    conn.close()


@pytest.mark.browser
def test_admin_migration_requires_preview_before_enabling_run(page, browser_server, tmp_path):
    kavita_db_path = tmp_path / "kavita-preview.db"
    _create_kavita_preview_db(kavita_db_path)

    session = browser_server["db_factory"]()
    try:
        admin = session.get(User, browser_server["seed"]["user_id"])
        admin.is_superuser = True
        session.commit()
    finally:
        session.close()

    try:
        page.goto(f"{browser_server['base_url']}/admin/migration", wait_until="networkidle")
        file_input = page.locator("#kavita-db-file")
        file_input.set_input_files(str(kavita_db_path))

        run_button = page.get_by_role("button", name="Start Migration")
        assert run_button.is_disabled()

        page.get_by_role("button", name="Preview Import").click()
        page.get_by_text("Import Preview").wait_for()
        page.get_by_text("1 new user to create").wait_for()
        page.get_by_text("1 existing user match").wait_for()
        page.get_by_text("1 existing user with importable progress").wait_for()
        assert not run_button.is_disabled()

        file_input.set_input_files([])
        page.get_by_text("Import Preview").wait_for(state="hidden")
        assert run_button.is_disabled()
    finally:
        session = browser_server["db_factory"]()
        try:
            admin = session.get(User, browser_server["seed"]["user_id"])
            if admin is not None:
                admin.is_superuser = False
                session.commit()
        finally:
            session.close()
