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

from app.core.security import get_password_hash, verify_password

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
        - Returns a CSV string of credentials if strategy is 'temp-password'.
        """
        logger.info("Starting User Migration...")

        # 1. Fetch Kavita Users
        kavita_users = self.kavita_conn.execute("SELECT Id, UserName, Email, IsAdmin FROM AspNetUsers").fetchall()

        # 2. Fetch Existing Parker Users (for conflict checking)
        existing_users = self.db.query(User).all()
        existing_map = {u.username.lower(): u for u in existing_users}

        created_credentials = []

        for k_user in kavita_users:
            username = k_user['UserName']
            email = k_user['Email']
            is_admin = k_user['IsAdmin'] == 1

            # Check if user already exists
            if username.lower() in existing_map:
                p_user = existing_map[username.lower()]
                self.user_map[k_user['Id']] = p_user.id
                logger.info(f"User match found: {username}")
                continue

            # Create New User
            temp_password = self._generate_temp_password()
            hashed_pw = get_password_hash(temp_password)

            new_user = User(
                username=username,
                email=email if email else None,
                hashed_password=hashed_pw,
                is_superuser=is_admin,
                is_active=True
            )

            self.db.add(new_user)
            self.db.flush()  # Flush to get the ID

            # Update Map
            self.user_map[k_user['Id']] = new_user.id

            # Store credential for CSV
            created_credentials.append({
                "username": username,
                "temporary_password": temp_password,
                "email": email or "N/A",
                "role": "Admin" if is_admin else "User"
            })

            logger.info(f"Created new user: {username}")

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
        parker_comics = self.db.query(
            Comic.id, Comic.file_path, Comic.number,
            Series.name.label("series_name")
        ).join(Volume).join(Series).all()

        # Index by Path and Metadata
        p_by_path = {str(c.file_path): c.id for c in parker_comics}
        p_by_meta = {f"{c.series_name.lower()}|{c.number}": c.id for c in parker_comics}

        # 2. Load Kavita Chapters (Remote DB)
        # We join to MangaFile to get the path
        query = """
                SELECT c.Id   as chapter_id, \
                       c.Number, \
                       s.Name as series_name, \
                       mf.FilePath
                FROM Chapter c
                         JOIN Volume v ON c.VolumeId = v.Id
                         JOIN Series s ON v.SeriesId = s.Id
                         LEFT JOIN MangaFile mf ON c.Id = mf.ChapterId
                WHERE mf.FilePath IS NOT NULL \
                """
        kavita_chapters = self.kavita_conn.execute(query).fetchall()

        count = 0
        for k_chap in kavita_chapters:
            # Strategy A: File Path Match
            if k_chap['FilePath'] in p_by_path:
                self.comic_map[k_chap['chapter_id']] = p_by_path[k_chap['FilePath']]
                count += 1
                continue

            # Strategy B: Metadata Match
            meta_key = f"{k_chap['series_name'].lower()}|{k_chap['Number']}"
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
            self.migrate_users()  # Run user migration if skipped (will just map existing)
        if not self.comic_map:
            self.map_comics()

        logger.info("Migrating progress...")

        # Fetch Kavita Progress
        query = "SELECT AppUserId, ChapterId, PagesRead, LastModified FROM AppUserProgress WHERE PagesRead > 0"
        records = self.kavita_conn.execute(query).fetchall()

        stats = {"inserted": 0, "updated": 0, "skipped": 0}

        for rec in records:
            k_uid = rec['AppUserId']
            k_cid = rec['ChapterId']

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

            if existing:
                # Update only if Kavita is newer or further along
                if pages_read > existing.current_page:
                    existing.current_page = pages_read
                    existing.last_read_at = last_read_dt
                    # If pages_read is high, assume complete?
                    # Ideally we check comic.page_count here.
                    comic = self.db.query(Comic).get(p_cid)
                    if comic and pages_read >= comic.page_count:
                        existing.completed = True

                    stats['updated'] += 1
                else:
                    stats['skipped'] += 1
            else:
                # Insert New
                # We need page count to determine completion
                comic = self.db.query(Comic).get(p_cid)
                is_completed = False
                if comic and pages_read >= comic.page_count:
                    is_completed = True

                new_prog = ReadingProgress(
                    user_id=p_uid,
                    comic_id=p_cid,
                    current_page=pages_read,
                    completed=is_completed,
                    last_read_at=last_read_dt
                )
                self.db.add(new_prog)
                stats['inserted'] += 1

        self.db.commit()
        return stats