import sqlite3
import secrets
import string
import csv
import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple, Any

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.comic_helpers import NON_PLAIN_FORMATS
from app.core.security import get_password_hash, verify_password

from app.models.library import Library
from app.models.user import User
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.reading_progress import ReadingProgress


logger = logging.getLogger(__name__)


class KavitaMigrationService:
    """
    Unified Service to migrate Users and Reading Progress from Kavita to Parker.
    Designed to be run from the Admin Dashboard via an existing SQLAlchemy Session.
    """

    def __init__(self, db: Session, kavita_db_path: str):
        self.db = db  # The active SQLAlchemy session
        self.kavita_db_path = Path(kavita_db_path)

        if not self.kavita_db_path.exists():
            raise FileNotFoundError(f"Kavita DB not found at {kavita_db_path}")

        # Connect to Kavita (ReadOnly)
        self.kavita_conn = sqlite3.connect(f"file:{self.kavita_db_path}?mode=ro", uri=True)
        self.kavita_conn.row_factory = sqlite3.Row

        # State
        self.user_map: Dict[int, int] = {}  # Kavita UserID -> Parker UserID
        self.comic_map: Dict[int, int] = {}  # Kavita ChapterID -> Parker ComicID

    def close(self):
        if self.kavita_conn:
            self.kavita_conn.close()

    # ==========================================
    # PHASE 1: USER MIGRATION
    # ==========================================

    def migrate_users(self, strategy: str = "temp-password") -> Optional[str]:
        """
        Migrates users from Kavita to Parker.
        - Creates non-existent users.
        - Checks AspNetUserRoles to determine Admin status (Role ID 1).
        - Returns a CSV string of credentials if strategy is 'temp-password'.
        """
        logger.info("Starting User Migration...")

        # 1. Fetch Kavita Users (Base Table)
        # Note: No 'IsAdmin' column here.
        kavita_users = self.kavita_conn.execute("SELECT Id, UserName, Email FROM AspNetUsers").fetchall()

        # 2. Identify Admins
        # In Kavita, Role ID 1 is the Administrator role.
        # We fetch the set of all UserIds that have this role.
        try:
            admin_rows = self.kavita_conn.execute("SELECT UserId FROM AspNetUserRoles WHERE RoleId = 1").fetchall()
            admin_ids = {row['UserId'] for row in admin_rows}
        except sqlite3.OperationalError:
            # Fallback if roles table is missing or different
            logger.warning("Could not query AspNetUserRoles. Defaulting all migrated users to standard role.")
            admin_ids = set()

        # 3. Fetch Existing Parker Users (for conflict checking)
        existing_users = self.db.query(User).all()
        existing_map = {u.username.lower(): u for u in existing_users}

        created_credentials = []

        for k_user in kavita_users:

            username = k_user['UserName']
            # Skip if username is missing/null
            if not username:
                continue

            email = k_user['Email']
            k_id = k_user['Id']

            # Determine Admin Status based on the Role lookup
            is_admin = k_id in admin_ids

            p_user = None

            # A. Get or Create User
            if username.lower() in existing_map:
                p_user = existing_map[username.lower()]
                self.user_map[k_id] = p_user.id
                logger.info(f"User match found: {username}")
            else:
                # Create New User
                temp_password = self._generate_temp_password()
                hashed_pw = get_password_hash(temp_password)

                p_user = User(
                    username=username,
                    email=email if email else None,
                    hashed_password=hashed_pw,
                    is_superuser=is_admin,
                    is_active=True
                )

                self.db.add(p_user)
                self.db.flush()  # Flush to get the ID

                # Update Map
                self.user_map[k_id] = p_user.id

                # Store credential for CSV
                created_credentials.append({
                    "username": username,
                    "temporary_password": temp_password,
                    "email": email or "N/A",
                    "role": "Admin" if is_admin else "User"
                })

                logger.info(f"Created new user: {username}")

            # B. Sync Library Permissions (Critical for Non-Admins)
            # In Parker, Admins (is_superuser) usually have implicit access to everything.
            # We only strictly need to map libraries for standard users.
            if p_user and not p_user.is_superuser:
                self._sync_library_permissions(k_id, p_user)

        self.db.commit()

        # Generate CSV if we created new users
        if created_credentials:
            output = io.StringIO()
            fieldnames = ["username", "temporary_password", "email", "role"]
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(created_credentials)
            return output.getvalue()

        return None

    def _sync_library_permissions(self, kavita_user_id: int, parker_user: User):
        """
        Looks up library access in Kavita and mirrors it to Parker.
        """
        # 1. Get Kavita Library IDs for this user
        # Table is usually 'AppUserLibrary' with columns 'AppUsersId', 'LibrariesId'
        try:
            rows = self.kavita_conn.execute(
                "SELECT LibrariesId FROM AppUserLibrary WHERE AppUsersId = ?",
                (kavita_user_id,)
            ).fetchall()
            allowed_k_libs = [r['LibrariesId'] for r in rows]
        except sqlite3.OperationalError:
            # Fallback: older Kavita versions or different schema?
            # If table doesn't exist, assume no restrictions or handle gracefully.
            logger.warning("Could not find AppUserLibrary table in Kavita DB. Skipping permission sync.")
            return

        if not allowed_k_libs:
            return

        # 2. Map Kavita Library ID -> Name
        # We fetch all libraries to build a lookup map
        try:
            k_libs = self.kavita_conn.execute("SELECT Id, Name FROM Library").fetchall()
            k_lib_map = {row['Id']: row['Name'] for row in k_libs}
        except sqlite3.OperationalError:
            return

        # 3. Find Matching Parker Libraries
        parker_libs = self.db.query(Library).all()
        p_lib_map = {lib.name.lower(): lib for lib in parker_libs}

        # 4. Assign Permissions
        # We clear existing (or append? Safe to clear and re-set for migration consistency)
        # parker_user.accessible_libraries = []

        for k_lib_id in allowed_k_libs:
            if k_lib_id in k_lib_map:
                k_name = k_lib_map[k_lib_id]
                # Match by Name (Case Insensitive)
                if k_name.lower() in p_lib_map:
                    matched_lib = p_lib_map[k_name.lower()]

                    # Avoid duplicates
                    if matched_lib not in parker_user.accessible_libraries:
                        parker_user.accessible_libraries.append(matched_lib)
                        logger.info(f"Granted access to library '{matched_lib.name}' for user '{parker_user.username}'")

    def _generate_temp_password(self, length=12):
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for i in range(length))

    # ==========================================
    # PHASE 2: COMIC MAPPING
    # ==========================================

    def map_comics(self) -> int:
        """
        Builds a map between Kavita ChapterIDs and Parker ComicIDs.
        Tries to match by File Path first, then Series+Number.
        """
        logger.info("Mapping comics...")

        # 1. Load Parker Comics (Local DB)
        # We fetch only what we need to minimize memory usage
        # fetch 'format' to distinguish Annuals
        parker_comics = self.db.query(
            Comic.id, Comic.file_path, Comic.number, Comic.format,
            Series.name.label("series_name")
        ).select_from(Comic).join(Comic.volume).join(Volume.series).all()

        # Index by Path and Metadata
        p_by_path = {str(c.file_path): c.id for c in parker_comics}
        p_by_meta = {}

        for c in parker_comics:
            # Determine if this is a "Special" format (Annual, Special, etc)
            # Normalize to lowercase for safety
            fmt = (c.format or "").lower()
            is_special = fmt in NON_PLAIN_FORMATS

            # Create a composite key: "series|number|is_special"
            # This allows "Superman|6|False" and "Superman|6|True" to coexist.
            key = f"{c.series_name.lower()}|{c.number}|{is_special}"
            p_by_meta[key] = c.id


        # 2. Load Kavita Chapters (Remote DB)
        # We join to MangaFile to get the path
        query = """
                SELECT c.Id   as chapter_id, \
                       c.Number, \
                       s.Name as series_name, \
                       mf.FilePath, \
                        v.Name as volume_name, v.Number as volume_number
                FROM Chapter c
                         JOIN Volume v ON c.VolumeId = v.Id
                         JOIN Series s ON v.SeriesId = s.Id
                         LEFT JOIN MangaFile mf ON c.Id = mf.ChapterId
                WHERE mf.FilePath IS NOT NULL \
                """
        kavita_chapters = self.kavita_conn.execute(query).fetchall()

        count = 0
        for k_chap in kavita_chapters:
            # Strategy A: File Path Match (Best)
            if k_chap['FilePath'] in p_by_path:
                self.comic_map[k_chap['chapter_id']] = p_by_path[k_chap['FilePath']]
                count += 1
                continue

            # Strategy B: Metadata Match (Fallback)
            # Detect Kavita's "Virtual Volume" logic for specials
            vol_name = str(k_chap['volume_name'])
            vol_num = k_chap['volume_number']

            # Kavita uses Name='100000' for specials bucket
            # We also check for volume 0 just in case
            k_is_special = (vol_name == '100000' or vol_num == 0)

            # Construct the matching key
            meta_key = f"{k_chap['series_name'].lower()}|{k_chap['Number']}|{k_is_special}"

            if meta_key in p_by_meta:
                self.comic_map[k_chap['chapter_id']] = p_by_meta[meta_key]
                count += 1

        logger.info(f"Mapped {count} comics out of {len(kavita_chapters)} in Kavita.")
        return count

    # ==========================================
    # PHASE 3: PROGRESS MIGRATION
    # ==========================================

    def migrate_progress(self) -> Dict[str, int]:
        """
        Migrates reading progress for mapped users and comics.
        """
        # Ensure mapping is done
        if not self.user_map:
            self.migrate_users() # Run user migration if skipped (will just map existing)
        if not self.comic_map:
            self.map_comics()

        logger.info("Migrating progress...")

        # Fetch Kavita Progress
        query = "SELECT AppUserId, ChapterId, PagesRead, LastModified FROM AppUserProgresses WHERE PagesRead > 0"
        records = self.kavita_conn.execute(query).fetchall()

        stats = {"inserted": 0, "updated": 0, "skipped": 0}

        for rec in records:
            k_uid = rec['AppUserId']
            k_cid = rec['ChapterId']
            pages_read = rec['PagesRead']

            # Skip unmapped items
            if k_uid not in self.user_map or k_cid not in self.comic_map:
                stats['skipped'] += 1
                continue

            p_uid = self.user_map[k_uid]
            p_cid = self.comic_map[k_cid]

            pages_read = rec['PagesRead']

            # Default completion logic: Assume completed if pages_read > 0 and no total available?
            # Better: Let Parker handle completion logic or fetch totals.
            # For migration simplicity, if it's the last page, it's done.
            # We'll rely on Parker's Comic.page_count if needed, but for now lets trust Kavita.

            last_read_str = rec['LastModified']
            # Ensure proper datetime format
            try:
                # Kavita format example: '2023-01-01 12:00:00' or ISO
                last_read_dt = datetime.fromisoformat(last_read_str)
            except:
                last_read_dt = datetime.now()

            # Check existing Parker Progress
            existing = self.db.query(ReadingProgress).filter_by(user_id=p_uid, comic_id=p_cid).first()

            # Fetch comic to get page count
            comic = self.db.query(Comic).get(p_cid)

            # Calculate Total Pages (Fallback Logic)
            # Priority: 1. Parker Comic Page Count, 2. Kavita Pages Read, 3. Default 1
            if comic and comic.page_count and comic.page_count > 0:
                total_pages = comic.page_count
            else:
                total_pages = max(pages_read, 1)

            # Calculate Completion
            is_completed = False
            if pages_read >= total_pages:
                is_completed = True

            if existing:
                # Update only if Kavita is newer or further along
                if pages_read > existing.current_page:
                    existing.current_page = pages_read
                    existing.total_pages = total_pages  # Update total pages too
                    existing.last_read_at = last_read_dt
                    existing.completed = is_completed  # Update completion status

                    stats['updated'] += 1
                else:
                    stats['skipped'] += 1
            else:
                # Insert New
                new_prog = ReadingProgress(
                    user_id=p_uid,
                    comic_id=p_cid,
                    current_page=pages_read,
                    total_pages=total_pages,
                    completed=is_completed,
                    last_read_at=last_read_dt
                )

                self.db.add(new_prog)
                stats['inserted'] += 1

        self.db.commit()
        return stats