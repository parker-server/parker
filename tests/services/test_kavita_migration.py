import sqlite3
from datetime import datetime, timedelta, timezone

from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.reading_progress import ReadingProgress
from app.models.series import Series
from app.models.user import User
from app.services.kavita_migration import KavitaMigrationService


def _create_kavita_db(tmp_path, *, include_user_tables=True, include_mapping_tables=True, include_progress_table=True):
    db_path = tmp_path / "kavita.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    if include_user_tables:
        cur.executescript(
            """
            CREATE TABLE AspNetUsers (Id INTEGER PRIMARY KEY, UserName TEXT, Email TEXT);
            CREATE TABLE AspNetUserRoles (UserId INTEGER, RoleId INTEGER);
            CREATE TABLE AspNetRoles (Id INTEGER PRIMARY KEY, Name TEXT, NormalizedName TEXT);
            CREATE TABLE AppUserLibrary (AppUsersId INTEGER, LibrariesId INTEGER);
            CREATE TABLE Library (Id INTEGER PRIMARY KEY, Name TEXT);
            """
        )

    if include_mapping_tables:
        cur.executescript(
            """
            CREATE TABLE Series (Id INTEGER PRIMARY KEY, Name TEXT);
            CREATE TABLE Volume (Id INTEGER PRIMARY KEY, SeriesId INTEGER, Name TEXT, Number INTEGER);
            CREATE TABLE Chapter (Id INTEGER PRIMARY KEY, VolumeId INTEGER, Number TEXT, IsSpecial INTEGER);
            CREATE TABLE MangaFile (Id INTEGER PRIMARY KEY, ChapterId INTEGER, FilePath TEXT);
            """
        )

    if include_progress_table:
        cur.executescript(
            """
            CREATE TABLE AppUserProgresses (
                Id INTEGER PRIMARY KEY,
                AppUserId INTEGER,
                ChapterId INTEGER,
                PagesRead INTEGER,
                LastModified TEXT
            );
            """
        )

    conn.commit()
    conn.close()
    return db_path


def _seed_parker_series(db, *, prefix: str):
    lib = Library(name=f"{prefix}-lib", path=f"/tmp/{prefix}-lib")
    series = Series(name=f"{prefix}-series", library=lib)
    volume = Volume(series=series, volume_number=1)
    db.add_all([lib, series, volume])
    db.flush()
    return lib, series, volume


def test_migrate_users_creates_admin_and_syncs_library_permissions(db, tmp_path):
    parker_lib = Library(name="Main Library", path="/tmp/main-library")
    db.add(parker_lib)
    db.commit()

    kavita_path = _create_kavita_db(tmp_path, include_mapping_tables=False, include_progress_table=False)
    conn = sqlite3.connect(kavita_path)
    cur = conn.cursor()

    cur.executemany(
        "INSERT INTO AspNetUsers (Id, UserName, Email) VALUES (?, ?, ?)",
        [
            (1, "admin_k", "admin_k@example.com"),
            (2, "reader_k", "reader_k@example.com"),
        ],
    )
    cur.executemany(
        "INSERT INTO AspNetRoles (Id, Name, NormalizedName) VALUES (?, ?, ?)",
        [
            (1, "Admin", "ADMIN"),
            (2, "Pleb", "PLEB"),
        ],
    )
    cur.executemany(
        "INSERT INTO AspNetUserRoles (UserId, RoleId) VALUES (?, ?)",
        [
            (1, 1),
            (2, 2),
        ],
    )
    cur.executemany(
        "INSERT INTO Library (Id, Name) VALUES (?, ?)",
        [
            (10, "Main Library"),
            (11, "Unknown Library"),
        ],
    )
    cur.execute("INSERT INTO AppUserLibrary (AppUsersId, LibrariesId) VALUES (?, ?)", (2, 10))
    conn.commit()
    conn.close()

    service = KavitaMigrationService(db=db, kavita_db_path=str(kavita_path))
    try:
        csv_data = service.migrate_users(strategy="temp-password")
    finally:
        service.close()

    assert csv_data is not None
    assert "admin_k" in csv_data
    assert "reader_k" in csv_data

    admin_user = db.query(User).filter(User.username == "admin_k").first()
    reader_user = db.query(User).filter(User.username == "reader_k").first()

    assert admin_user is not None
    assert admin_user.is_superuser is True
    assert reader_user is not None
    assert reader_user.is_superuser is False
    assert [lib.name for lib in reader_user.accessible_libraries] == ["Main Library"]


def test_map_comics_uses_suffix_then_unique_metadata_and_skips_ambiguous(db, tmp_path):
    _, batman_series, batman_volume = _seed_parker_series(db, prefix="kmap-batman")
    batman_series.name = "Batman"

    _, flash_series, flash_volume = _seed_parker_series(db, prefix="kmap-flash")
    flash_series.name = "Flash"

    _, ambig_series, ambig_volume = _seed_parker_series(db, prefix="kmap-ambig")
    ambig_series.name = "X-Men"

    c_suffix = Comic(
        volume_id=batman_volume.id,
        number="1",
        format=None,
        title="Batman 1",
        filename="batman-1.cbz",
        file_path="D:/comics/DC/Batman/Batman #001.cbz",
        page_count=20,
    )
    c_meta = Comic(
        volume_id=flash_volume.id,
        number="5",
        format="annual",
        title="Flash Annual 5",
        filename="flash-annual-5.cbz",
        file_path="D:/comics/DC/Flash/Flash Annual #005.cbz",
        page_count=30,
    )
    c_ambig_a = Comic(
        volume_id=ambig_volume.id,
        number="7",
        format=None,
        title="X-Men 7A",
        filename="xmen-7a.cbz",
        file_path="D:/comics/Marvel/X-Men/X-Men #007-a.cbz",
        page_count=22,
    )
    c_ambig_b = Comic(
        volume_id=ambig_volume.id,
        number="7",
        format=None,
        title="X-Men 7B",
        filename="xmen-7b.cbz",
        file_path="D:/comics/Marvel/X-Men/X-Men #007-b.cbz",
        page_count=22,
    )

    db.add_all([c_suffix, c_meta, c_ambig_a, c_ambig_b])
    db.commit()

    kavita_path = _create_kavita_db(tmp_path, include_user_tables=False, include_progress_table=False)
    conn = sqlite3.connect(kavita_path)
    cur = conn.cursor()

    cur.executemany(
        "INSERT INTO Series (Id, Name) VALUES (?, ?)",
        [
            (1, "Batman"),
            (2, "Flash"),
            (3, "X-Men"),
        ],
    )
    cur.executemany(
        "INSERT INTO Volume (Id, SeriesId, Name, Number) VALUES (?, ?, ?, ?)",
        [
            (11, 1, "1", 1),
            (21, 2, "100000", 0),
            (31, 3, "1", 1),
        ],
    )
    cur.executemany(
        "INSERT INTO Chapter (Id, VolumeId, Number, IsSpecial) VALUES (?, ?, ?, ?)",
        [
            (101, 11, "1", 0),
            (102, 11, "1", 0),
            (201, 21, "5", 1),
            (301, 31, "7", 0),
        ],
    )
    cur.executemany(
        "INSERT INTO MangaFile (Id, ChapterId, FilePath) VALUES (?, ?, ?)",
        [
            (1001, 101, "/comics/DC/Batman/Batman #001.cbz"),
            (1002, 102, "/comics/DC/Batman/Batman #001.cbz"),
        ],
    )
    conn.commit()
    conn.close()

    service = KavitaMigrationService(db=db, kavita_db_path=str(kavita_path))
    try:
        mapped = service.map_comics()
    finally:
        service.close()

    assert mapped == 2
    assert set(service.comic_map.keys()) == {101, 201}
    assert service.mapping_stats["path_suffix_matches"] == 1
    assert service.mapping_stats["metadata_matches"] == 1
    assert service.mapping_stats["ambiguous_metadata_matches"] == 2
    assert service.mapping_stats["target_conflicts"] == 1


def test_migrate_progress_normalizes_zero_based_and_updates_existing(db, tmp_path):
    _, series, volume = _seed_parker_series(db, prefix="kprog")
    series.name = "Progress Series"

    user = User(
        username="prog-user",
        email="prog-user@example.com",
        hashed_password="hash",
        is_superuser=False,
        is_active=True,
    )
    db.add(user)
    db.flush()

    c_new = Comic(
        volume_id=volume.id,
        number="1",
        title="Progress New",
        filename="progress-new.cbz",
        file_path="D:/comics/progress-new.cbz",
        page_count=10,
    )
    c_existing = Comic(
        volume_id=volume.id,
        number="2",
        title="Progress Existing",
        filename="progress-existing.cbz",
        file_path="D:/comics/progress-existing.cbz",
        page_count=8,
    )
    db.add_all([c_new, c_existing])
    db.flush()

    existing_progress = ReadingProgress(
        user_id=user.id,
        comic_id=c_existing.id,
        current_page=2,
        total_pages=8,
        completed=False,
        last_read_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.add(existing_progress)
    db.commit()

    kavita_path = _create_kavita_db(tmp_path, include_user_tables=False, include_mapping_tables=False)
    conn = sqlite3.connect(kavita_path)
    cur = conn.cursor()
    cur.executemany(
        "INSERT INTO AppUserProgresses (AppUserId, ChapterId, PagesRead, LastModified) VALUES (?, ?, ?, ?)",
        [
            (1, 100, 10, "2026-01-01 10:00:00"),
            (1, 200, 6, "2026-01-02 10:00:00"),
            (1, 999, 3, "2026-01-03 10:00:00"),
        ],
    )
    conn.commit()
    conn.close()

    service = KavitaMigrationService(db=db, kavita_db_path=str(kavita_path))
    service.user_map = {1: user.id}
    service.comic_map = {100: c_new.id, 200: c_existing.id}

    try:
        stats = service.migrate_progress()
    finally:
        service.close()

    assert stats["inserted"] == 1
    assert stats["updated"] == 1
    assert stats["skipped"] == 1

    db.flush()
    new_progress = db.query(ReadingProgress).filter_by(user_id=user.id, comic_id=c_new.id).first()
    updated_progress = db.query(ReadingProgress).filter_by(user_id=user.id, comic_id=c_existing.id).first()

    assert new_progress is not None
    assert new_progress.current_page == 9
    assert new_progress.total_pages == 10
    assert new_progress.completed is True

    assert updated_progress is not None
    assert updated_progress.current_page == 5
    assert updated_progress.total_pages == 8
    assert updated_progress.completed is False



