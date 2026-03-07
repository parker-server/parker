import csv
import io
import logging
import secrets
import sqlite3
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.core.comic_helpers import NON_PLAIN_FORMATS
from app.core.security import get_password_hash
from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.reading_progress import ReadingProgress
from app.models.series import Series
from app.models.user import User


logger = logging.getLogger(__name__)


class KavitaMigrationService:
    """
    Migrate users and reading progress from Kavita into Parker.

    Flow:
    1) migrate_users() to create/map users
    2) map_comics() to map Kavita Chapter IDs to Parker Comic IDs
    3) migrate_progress() to move reading progress

    Notes:
    - This service does not commit/rollback transactions.
      Caller controls transaction boundaries.
    """

    PATH_SUFFIX_MIN_PARTS = 3
    PATH_SUFFIX_MAX_PARTS = 8

    def __init__(self, db: Session, kavita_db_path: str):
        self.db = db
        self.kavita_db_path = Path(kavita_db_path)

        if not self.kavita_db_path.exists():
            raise FileNotFoundError(f"Kavita DB not found at {kavita_db_path}")

        self.kavita_conn = sqlite3.connect(f"file:{self.kavita_db_path}?mode=ro", uri=True)
        self.kavita_conn.row_factory = sqlite3.Row

        self.user_map: Dict[int, int] = {}
        self.comic_map: Dict[int, int] = {}

        self.mapping_stats = {
            "total_kavita_chapters": 0,
            "mapped_total": 0,
            "unmapped_total": 0,
            "path_exact_matches": 0,
            "path_suffix_matches": 0,
            "metadata_matches": 0,
            "ambiguous_path_matches": 0,
            "ambiguous_metadata_matches": 0,
            "target_conflicts": 0,
        }

    def close(self):
        if self.kavita_conn:
            self.kavita_conn.close()

    # ==========================================
    # PHASE 1: USER MIGRATION
    # ==========================================

    def migrate_users(self, strategy: str = "temp-password") -> Optional[str]:
        """
        Migrate users from Kavita.

        Supported strategy:
        - temp-password: create new users with random temporary passwords and
          return CSV credentials for newly created users.
        """
        if strategy != "temp-password":
            raise ValueError("Unsupported strategy. Only 'temp-password' is currently supported.")

        logger.info("Starting user migration")

        kavita_users = self.kavita_conn.execute("SELECT Id, UserName, Email FROM AspNetUsers").fetchall()
        admin_ids = self._get_admin_user_ids()

        existing_users = self.db.query(User).all()
        existing_map = {u.username.lower(): u for u in existing_users if u.username}

        created_credentials = []

        for k_user in kavita_users:
            username = k_user["UserName"]
            if not username:
                continue

            email = k_user["Email"]
            k_id = k_user["Id"]
            is_admin = k_id in admin_ids

            if username.lower() in existing_map:
                p_user = existing_map[username.lower()]
                self.user_map[k_id] = p_user.id
                logger.info("User match found: %s", username)
            else:
                # Create New User
                temp_password = self._generate_temp_password()
                hashed_pw = get_password_hash(temp_password)

                p_user = User(
                    username=username,
                    email=email if email else None,
                    hashed_password=hashed_pw,
                    is_superuser=is_admin,
                    is_active=True,
                )

                self.db.add(p_user)
                self.db.flush()  # Flush to get the ID

                # Update Map
                self.user_map[k_id] = p_user.id

                created_credentials.append(
                    {
                        "username": username,
                        "temporary_password": temp_password,
                        "email": email or "N/A",
                        "role": "Admin" if is_admin else "User",
                    }
                )

                logger.info("Created new user: %s", username)

            if not p_user.is_superuser:
                self._sync_library_permissions(k_id, p_user)

        # Generate CSV if we created new users
        if created_credentials:
            output = io.StringIO()
            fieldnames = ["username", "temporary_password", "email", "role"]
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(created_credentials)
            return output.getvalue()

        return None

    def _get_admin_user_ids(self) -> set[int]:
        """Resolve admin users from role names; fallback to RoleId=1."""
        try:
            rows = self.kavita_conn.execute(
                """
                SELECT ur.UserId
                FROM AspNetUserRoles ur
                JOIN AspNetRoles r ON ur.RoleId = r.Id
                WHERE UPPER(COALESCE(r.NormalizedName, r.Name)) = 'ADMIN'
                """
            ).fetchall()
            return {row["UserId"] for row in rows}
        except sqlite3.OperationalError:
            logger.warning("Could not resolve roles by name, falling back to RoleId=1.")
            try:
                rows = self.kavita_conn.execute("SELECT UserId FROM AspNetUserRoles WHERE RoleId = 1").fetchall()
                return {row["UserId"] for row in rows}
            except sqlite3.OperationalError:
                logger.warning("Could not query AspNetUserRoles. Defaulting all users to non-admin.")
                return set()

    def _sync_library_permissions(self, kavita_user_id: int, parker_user: User):
        """Mirror Kavita library access onto Parker user library access."""
        try:
            rows = self.kavita_conn.execute(
                "SELECT LibrariesId FROM AppUserLibrary WHERE AppUsersId = ?",
                (kavita_user_id,),
            ).fetchall()
            allowed_k_libs = [r["LibrariesId"] for r in rows]
        except sqlite3.OperationalError:
            logger.warning("Could not query AppUserLibrary. Skipping permission sync.")
            return

        if not allowed_k_libs:
            return

        # 2. Map Kavita Library ID -> Name
        # We fetch all libraries to build a lookup map
        try:
            k_libs = self.kavita_conn.execute("SELECT Id, Name FROM Library").fetchall()
            k_lib_map = {row["Id"]: row["Name"] for row in k_libs}
        except sqlite3.OperationalError:
            logger.warning("Could not query Library table. Skipping permission sync.")
            return

        # 3. Find Matching Parker Libraries
        parker_libs = self.db.query(Library).all()
        p_lib_map = {lib.name.lower(): lib for lib in parker_libs if lib.name}

        for k_lib_id in allowed_k_libs:
            k_name = k_lib_map.get(k_lib_id)
            if not k_name:
                continue

            matched_lib = p_lib_map.get(k_name.lower())
            if matched_lib and matched_lib not in parker_user.accessible_libraries:
                parker_user.accessible_libraries.append(matched_lib)
                logger.info("Granted '%s' access to '%s'", matched_lib.name, parker_user.username)

    def _generate_temp_password(self, length: int = 14) -> str:
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    # ==========================================
    # PHASE 2: COMIC MAPPING
    # ==========================================

    def _normalize_path(self, path_value: Optional[str]) -> str:
        if not path_value:
            return ""

        normalized = path_value.replace("\\", "/").strip().lower()
        while "//" in normalized:
            normalized = normalized.replace("//", "/")
        return normalized

    def _path_parts(self, normalized_path: str) -> tuple[str, ...]:
        if not normalized_path:
            return tuple()
        return tuple(part for part in normalized_path.split("/") if part)

    def _build_meta_key(self, series_name: Optional[str], number: Optional[str], is_special: bool) -> str:
        return f"{(series_name or '').lower()}|{number}|{1 if is_special else 0}"

    def _register_mapping(self, chapter_id: int, comic_id: int, source: str, used_target_comics: set[int]) -> bool:
        if chapter_id in self.comic_map:
            return False

        if comic_id in used_target_comics:
            self.mapping_stats["target_conflicts"] += 1
            return False

        self.comic_map[chapter_id] = comic_id
        used_target_comics.add(comic_id)

        if source == "path_exact":
            self.mapping_stats["path_exact_matches"] += 1
        elif source == "path_suffix":
            self.mapping_stats["path_suffix_matches"] += 1
        elif source == "metadata":
            self.mapping_stats["metadata_matches"] += 1

        return True

    def map_comics(self) -> int:
        """
        Build a conservative Chapter->Comic map:
        1) path exact
        2) path suffix (for differing root directories)
        3) metadata fallback (series + number + is_special) only when unique on both sides
        """
        logger.info("Mapping comics")

        self.comic_map.clear()
        for key in self.mapping_stats:
            self.mapping_stats[key] = 0

        parker_comics = (
            self.db.query(
                Comic.id,
                Comic.file_path,
                Comic.number,
                Comic.format,
                Series.name.label("series_name"),
            )
            .select_from(Comic)
            .join(Comic.volume)
            .join(Volume.series)
            .all()
        )

        path_index: dict[str, set[int]] = {}
        suffix_index: dict[tuple[str, ...], set[int]] = {}
        meta_index: dict[str, set[int]] = {}

        for comic in parker_comics:
            normalized = self._normalize_path(str(comic.file_path))
            if normalized:
                path_index.setdefault(normalized, set()).add(comic.id)

                parts = self._path_parts(normalized)
                max_parts = min(self.PATH_SUFFIX_MAX_PARTS, len(parts))
                for size in range(self.PATH_SUFFIX_MIN_PARTS, max_parts + 1):
                    suffix = parts[-size:]
                    suffix_index.setdefault(suffix, set()).add(comic.id)

            fmt = (comic.format or "").strip().lower()
            is_special = fmt in NON_PLAIN_FORMATS
            meta_key = self._build_meta_key(comic.series_name, comic.number, is_special)
            meta_index.setdefault(meta_key, set()).add(comic.id)

        kavita_chapters = self.kavita_conn.execute(
            """
            SELECT
                c.Id AS chapter_id,
                c.Number AS chapter_number,
                c.IsSpecial AS is_special,
                s.Name AS series_name,
                MIN(mf.FilePath) AS file_path
            FROM Chapter c
            JOIN Volume v ON c.VolumeId = v.Id
            JOIN Series s ON v.SeriesId = s.Id
            LEFT JOIN MangaFile mf ON c.Id = mf.ChapterId
            GROUP BY c.Id, c.Number, c.IsSpecial, s.Name
            """
        ).fetchall()

        self.mapping_stats["total_kavita_chapters"] = len(kavita_chapters)

        kavita_meta_counts: dict[str, int] = {}
        for row in kavita_chapters:
            meta_key = self._build_meta_key(row["series_name"], row["chapter_number"], bool(row["is_special"]))
            kavita_meta_counts[meta_key] = kavita_meta_counts.get(meta_key, 0) + 1

        used_target_comics: set[int] = set()

        for row in kavita_chapters:
            chapter_id = row["chapter_id"]
            matched_comic_id = None

            file_path = row["file_path"]
            if file_path:
                normalized = self._normalize_path(file_path)

                exact_candidates = path_index.get(normalized)
                if exact_candidates:
                    if len(exact_candidates) == 1:
                        matched_comic_id = next(iter(exact_candidates))
                        if self._register_mapping(chapter_id, matched_comic_id, "path_exact", used_target_comics):
                            continue
                    else:
                        self.mapping_stats["ambiguous_path_matches"] += 1
                else:
                    parts = self._path_parts(normalized)
                    max_parts = min(self.PATH_SUFFIX_MAX_PARTS, len(parts))

                    for size in range(max_parts, self.PATH_SUFFIX_MIN_PARTS - 1, -1):
                        suffix = parts[-size:]
                        candidates = suffix_index.get(suffix)
                        if not candidates:
                            continue

                        if len(candidates) == 1:
                            matched_comic_id = next(iter(candidates))
                        else:
                            self.mapping_stats["ambiguous_path_matches"] += 1
                        break

                    if matched_comic_id is not None:
                        if self._register_mapping(chapter_id, matched_comic_id, "path_suffix", used_target_comics):
                            continue

            meta_key = self._build_meta_key(row["series_name"], row["chapter_number"], bool(row["is_special"]))
            parker_meta_candidates = meta_index.get(meta_key, set())
            kavita_key_count = kavita_meta_counts.get(meta_key, 0)

            if len(parker_meta_candidates) == 1 and kavita_key_count == 1:
                matched_comic_id = next(iter(parker_meta_candidates))
                self._register_mapping(chapter_id, matched_comic_id, "metadata", used_target_comics)
            elif parker_meta_candidates and (len(parker_meta_candidates) > 1 or kavita_key_count > 1):
                self.mapping_stats["ambiguous_metadata_matches"] += 1

        self.mapping_stats["mapped_total"] = len(self.comic_map)
        self.mapping_stats["unmapped_total"] = max(
            self.mapping_stats["total_kavita_chapters"] - self.mapping_stats["mapped_total"],
            0,
        )

        logger.info(
            "Mapped %s/%s chapters (exact=%s, suffix=%s, metadata=%s, ambiguous_path=%s, ambiguous_metadata=%s, target_conflicts=%s)",
            self.mapping_stats["mapped_total"],
            self.mapping_stats["total_kavita_chapters"],
            self.mapping_stats["path_exact_matches"],
            self.mapping_stats["path_suffix_matches"],
            self.mapping_stats["metadata_matches"],
            self.mapping_stats["ambiguous_path_matches"],
            self.mapping_stats["ambiguous_metadata_matches"],
            self.mapping_stats["target_conflicts"],
        )

        return self.mapping_stats["mapped_total"]

    # ==========================================
    # PHASE 3: PROGRESS MIGRATION
    # ==========================================

    def _parse_kavita_datetime(self, raw_value: Optional[str]) -> datetime:
        if not raw_value:
            return datetime.now(timezone.utc)

        try:
            parsed = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _normalize_current_page(self, pages_read: int, total_pages: int) -> int:
        if total_pages <= 0:
            return 0

        # Kavita PagesRead is count-based; Parker current_page is zero-based.
        return max(0, min(pages_read - 1, total_pages - 1))

    def _is_newer(self, source_dt: datetime, target_dt: Optional[datetime]) -> bool:
        if target_dt is None:
            return True

        try:
            return source_dt > target_dt
        except TypeError:
            # Handle naive vs aware comparison mismatch conservatively.
            return True

    def migrate_progress(self) -> Dict[str, int]:
        """Migrate reading progress for mapped users and comics."""
        if not self.user_map:
            self.migrate_users(strategy="temp-password")
        if not self.comic_map:
            self.map_comics()

        logger.info("Migrating reading progress")

        records = self.kavita_conn.execute(
            "SELECT AppUserId, ChapterId, PagesRead, LastModified FROM AppUserProgresses WHERE PagesRead > 0"
        ).fetchall()

        mapped_comic_ids = set(self.comic_map.values())
        comic_page_map: dict[int, int] = {}

        if mapped_comic_ids:
            comic_rows = self.db.query(Comic.id, Comic.page_count).filter(Comic.id.in_(mapped_comic_ids)).all()
            comic_page_map = {row.id: (row.page_count or 0) for row in comic_rows}

        stats = {
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "mapped_users": len(self.user_map),
            "mapped_comics": len(self.comic_map),
        }

        for rec in records:
            k_user_id = rec["AppUserId"]
            k_chapter_id = rec["ChapterId"]
            pages_read = int(rec["PagesRead"] or 0)

            if k_user_id not in self.user_map or k_chapter_id not in self.comic_map:
                stats["skipped"] += 1
                continue

            p_user_id = self.user_map[k_user_id]
            p_comic_id = self.comic_map[k_chapter_id]

            total_pages = comic_page_map.get(p_comic_id, 0)
            if total_pages <= 0:
                total_pages = max(pages_read, 1)

            current_page = self._normalize_current_page(pages_read, total_pages)
            completed = pages_read >= total_pages
            last_read_at = self._parse_kavita_datetime(rec["LastModified"])

            existing = self.db.query(ReadingProgress).filter_by(user_id=p_user_id, comic_id=p_comic_id).first()

            if existing:
                changed = False

                new_total_pages = max(existing.total_pages or 0, total_pages)
                if new_total_pages != existing.total_pages:
                    existing.total_pages = new_total_pages
                    changed = True

                existing_current_page = existing.current_page or 0
                if current_page > existing_current_page:
                    existing.current_page = current_page
                    changed = True

                if completed and not existing.completed:
                    existing.completed = True
                    changed = True

                if self._is_newer(last_read_at, existing.last_read_at):
                    existing.last_read_at = last_read_at
                    changed = True

                if changed:
                    stats["updated"] += 1
                else:
                    stats["skipped"] += 1
            else:
                self.db.add(
                    ReadingProgress(
                        user_id=p_user_id,
                        comic_id=p_comic_id,
                        current_page=current_page,
                        total_pages=total_pages,
                        completed=completed,
                        last_read_at=last_read_at,
                    )
                )
                stats["inserted"] += 1

        # Include mapping diagnostics in response payload.
        stats.update({
            "mapping_total_chapters": self.mapping_stats["total_kavita_chapters"],
            "mapping_mapped_chapters": self.mapping_stats["mapped_total"],
            "mapping_unmapped_chapters": self.mapping_stats["unmapped_total"],
            "mapping_path_exact": self.mapping_stats["path_exact_matches"],
            "mapping_path_suffix": self.mapping_stats["path_suffix_matches"],
            "mapping_metadata": self.mapping_stats["metadata_matches"],
            "mapping_ambiguous_path": self.mapping_stats["ambiguous_path_matches"],
            "mapping_ambiguous_metadata": self.mapping_stats["ambiguous_metadata_matches"],
            "mapping_target_conflicts": self.mapping_stats["target_conflicts"],
        })

        return stats
