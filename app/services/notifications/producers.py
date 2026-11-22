"""
Built-in notification producers and the hooks that run them.

A producer decides who should hear about something and passes the data the
kind needs to NotificationService.notify(). Hooks run a list of producers and
keep one failing producer from stopping the others or the caller.
"""
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.core import setting_keys
from app.core.comic_helpers import filter_series_query_to_user, get_format_filters
from app.models.comic import Comic, Volume
from app.models.interactions import UserVolumeFollow
from app.models.library import Library
from app.models.reading_progress import ReadingProgress
from app.models.series import Series
from app.models.user import User
from app.services.notifications.service import NotificationService
from app.services.settings_service import SettingsService
from app.services.version_check import get_version_check_status, parse_version

logger = logging.getLogger(__name__)

WHATS_NEW_FILE = Path(__file__).resolve().parents[2] / "data" / "whats_new.json"
WHATS_NEW_AUDIENCES = ("all", "admin")
# Year in Review goes out on the first daily check on or after this day in
# December. A January check still sends it for the previous year.
YEAR_IN_REVIEW_START_DAY = 15
# A scan adding more plain issues than this to followed volumes is treated as a
# bulk import, not new issues followers are waiting for.
FOLLOW_ARRIVALS_BULK_LIMIT = 500

Version = tuple[int, int, int]


# --- What's New ---

@dataclass(frozen=True)
class WhatsNewEntry:
    version: str
    audience: str
    title: str
    body: str = ""
    link: Optional[str] = None

    @property
    def version_key(self) -> Version:
        return parse_version(self.version)  # type: ignore[return-value]


def load_whats_new_entries(path: Path = WHATS_NEW_FILE) -> list[WhatsNewEntry]:
    """Load What's New entries, newest version first. Invalid entries are skipped."""
    try:
        raw_entries = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        logger.warning("Could not read What's New entries from %s: %s", path, exc)
        return []

    entries: list[WhatsNewEntry] = []
    for raw in raw_entries if isinstance(raw_entries, list) else []:
        if not isinstance(raw, dict):
            continue
        version = str(raw.get("version") or "")
        audience = raw.get("audience", "all")
        title = str(raw.get("title") or "").strip()
        if parse_version(version) is None or audience not in WHATS_NEW_AUDIENCES or not title:
            logger.warning("Skipping invalid What's New entry: %s", raw)
            continue
        entries.append(WhatsNewEntry(
            version=version.lstrip("v"),
            audience=audience,
            title=title,
            body=str(raw.get("body") or "").strip(),
            link=raw.get("link") or None,
        ))

    # Newest version first; entries within a version keep their file order.
    return sorted(entries, key=lambda entry: entry.version_key, reverse=True)


def whats_new_for_user(
    is_admin: bool,
    entries: Optional[list[WhatsNewEntry]] = None,
    current_version: Optional[str] = None,
) -> list[WhatsNewEntry]:
    """Entries a user can see: released versions only, admin entries for admins only."""
    current = parse_version(current_version or settings.version)
    entries = load_whats_new_entries() if entries is None else entries
    return [
        entry for entry in entries
        if current is not None
        and entry.version_key <= current
        and (entry.audience == "all" or is_admin)
    ]


def announce_whats_new(
    db: Session,
    current_version: Optional[str] = None,
    entries: Optional[list[WhatsNewEntry]] = None,
) -> int:
    """
    Tell users about What's New entries added since the last announced version.

    Each user gets one notification per upgrade, however many versions were
    skipped. On a fresh install nothing is announced. On the first start of a
    server that already had libraries, the running version's entries are
    announced, so the release that adds notifications can introduce itself.
    """
    current = parse_version(current_version or settings.version)
    if current is None:
        return 0

    settings_service = SettingsService(db)
    last_raw = settings_service.get(setting_keys.NOTIFICATIONS_WHATS_NEW_LAST_VERSION)
    if last_raw is None:
        logger.warning("What's New skipped: %s is missing", setting_keys.NOTIFICATIONS_WHATS_NEW_LAST_VERSION)
        return 0

    last = parse_version(last_raw)
    if last is not None and last >= current:
        return 0

    entries = load_whats_new_entries() if entries is None else entries
    if last is None:
        is_fresh_install = db.query(Library.id).first() is None
        new_entries = [] if is_fresh_install else [e for e in entries if e.version_key == current]
    else:
        new_entries = [e for e in entries if last < e.version_key <= current]

    current_label = ".".join(str(part) for part in current)
    notified = 0
    if new_entries:
        notified = _notify_whats_new(db, new_entries, current_label)

    settings_service.update(setting_keys.NOTIFICATIONS_WHATS_NEW_LAST_VERSION, current_label)
    if new_entries:
        logger.info("Announced %s What's New entries to %s users", len(new_entries), notified)
    return notified


def _notify_whats_new(db: Session, entries: list[WhatsNewEntry], version_label: str) -> int:
    users = db.query(User.id, User.is_superuser).filter(User.is_active == True).all()  # noqa: E712
    reader_entries = [entry for entry in entries if entry.audience == "all"]
    service = NotificationService(db)

    notified = 0
    for audience_entries, user_ids in (
        (reader_entries, [user.id for user in users if not user.is_superuser]),
        (entries, [user.id for user in users if user.is_superuser]),
    ):
        if not audience_entries or not user_ids:
            continue
        notified += service.notify(
            "whats_new",
            user_ids,
            data={"version": version_label, "first_title": audience_entries[0].title},
            group_key="whats_new",
            count=len(audience_entries),
        )
    return notified


# --- Follow arrivals ---

@dataclass(frozen=True)
class ScanInfo:
    """What scan producers need to know about a finished library scan."""
    library_id: int
    started_at: datetime
    first_scan: bool


def notify_follow_arrivals(db: Session, scan: ScanInfo) -> int:
    """
    Tell followers about plain issues a scan added to volumes they follow.

    Each follower gets one notification per volume, and only for volumes they
    can see. A library's first scan and very large batches are skipped, since
    those are backfills rather than new issues.
    """
    if scan.first_scan:
        logger.info("Follow arrivals skipped: first scan of library %s", scan.library_id)
        return 0

    service = NotificationService(db)
    if not service.is_enabled():
        return 0

    is_plain, _, _ = get_format_filters()
    followed_volume_ids = db.query(UserVolumeFollow.volume_id).distinct()
    arrivals = (
        db.query(Comic.volume_id, func.count(Comic.id))
        .join(Volume, Volume.id == Comic.volume_id)
        .join(Series, Series.id == Volume.series_id)
        .filter(Series.library_id == scan.library_id)
        .filter(Comic.created_at >= scan.started_at)
        .filter(Comic.volume_id.in_(followed_volume_ids))
        .filter(is_plain)
        .group_by(Comic.volume_id)
        .all()
    )
    if not arrivals:
        return 0

    counts = {volume_id: count for volume_id, count in arrivals}
    total = sum(counts.values())
    if total > FOLLOW_ARRIVALS_BULK_LIMIT:
        logger.info(
            "Follow arrivals skipped: %s new issues in %s followed volumes looks like a bulk import",
            total, len(counts),
        )
        return 0

    follows = (
        db.query(UserVolumeFollow.volume_id, User)
        .join(User, User.id == UserVolumeFollow.user_id)
        .filter(UserVolumeFollow.volume_id.in_(counts))
        .filter(User.is_active == True)  # noqa: E712
        .all()
    )
    users_by_id: dict[int, User] = {}
    volume_ids_by_user: dict[int, list[int]] = defaultdict(list)
    for volume_id, user in follows:
        users_by_id[user.id] = user
        volume_ids_by_user[user.id].append(volume_id)

    # Library access and age rating apply per series, so a follower sees all of
    # a volume's new issues or none of them.
    user_ids_by_volume: dict[int, list[int]] = defaultdict(list)
    for user_id, volume_ids in volume_ids_by_user.items():
        visible = filter_series_query_to_user(
            db.query(Volume.id)
            .join(Series, Series.id == Volume.series_id)
            .filter(Volume.id.in_(volume_ids)),
            users_by_id[user_id],
        )
        for (volume_id,) in visible.all():
            user_ids_by_volume[volume_id].append(user_id)

    if not user_ids_by_volume:
        return 0

    volumes = (
        db.query(Volume.id, Volume.volume_number, Series.name)
        .join(Series, Series.id == Volume.series_id)
        .filter(Volume.id.in_(user_ids_by_volume))
        .all()
    )
    notified = 0
    for volume_id, volume_number, series_name in volumes:
        notified += service.notify(
            "follow_arrivals",
            user_ids_by_volume[volume_id],
            data={"volume_id": volume_id, "series_name": series_name, "volume_number": volume_number},
            group_key=f"follow:volume:{volume_id}",
            count=counts[volume_id],
        )
    return notified


# --- Daily checks ---

def year_in_review_year(today: date) -> Optional[int]:
    """The year whose review should be announced today, or None outside the season."""
    if today.month == 12 and today.day >= YEAR_IN_REVIEW_START_DAY:
        return today.year
    if today.month == 1:
        return today.year - 1
    return None


def announce_year_in_review(db: Session, today: date) -> int:
    """Tell users who finished a comic this year that their Year in Review is ready, once a year."""
    year = year_in_review_year(today)
    if year is None:
        return 0

    service = NotificationService(db)
    if not service.is_enabled():
        return 0

    settings_service = SettingsService(db)
    last_year = settings_service.get(setting_keys.NOTIFICATIONS_YEAR_IN_REVIEW_LAST_YEAR)
    if last_year is None:
        logger.warning("Year in Review skipped: %s is missing", setting_keys.NOTIFICATIONS_YEAR_IN_REVIEW_LAST_YEAR)
        return 0
    if last_year >= year:
        return 0

    user_ids = [
        user_id for (user_id,) in (
            db.query(ReadingProgress.user_id)
            .join(User, User.id == ReadingProgress.user_id)
            .filter(User.is_active == True)  # noqa: E712
            .filter(ReadingProgress.completed == True)  # noqa: E712
            .filter(ReadingProgress.last_read_at >= datetime(year, 1, 1))
            .filter(ReadingProgress.last_read_at < datetime(year + 1, 1, 1))
            .group_by(ReadingProgress.user_id)
            .all()
        )
    ]
    notified = 0
    if user_ids:
        notified = service.notify(
            "year_in_review",
            user_ids,
            data={"year": year},
            group_key=f"year_in_review:{year}",
        )

    settings_service.update(setting_keys.NOTIFICATIONS_YEAR_IN_REVIEW_LAST_YEAR, year)
    logger.info("Announced the %s Year in Review to %s users", year, notified)
    return notified


def announce_update_available(db: Session, today: date, current_version: Optional[str] = None) -> int:
    """
    Tell admins once about each newer Parker release found by the version check.

    The check contacts GitHub at most every few hours (its result is cached), and
    not at all when update checks or notifications are turned off.
    """
    service = NotificationService(db)
    if not service.is_enabled():
        return 0

    settings_service = SettingsService(db)
    if settings_service.get(setting_keys.SERVER_UPDATE_CHECK_ENABLED) is False:
        return 0
    last_raw = settings_service.get(setting_keys.NOTIFICATIONS_UPDATE_LAST_VERSION)
    if last_raw is None:
        logger.warning("Update notification skipped: %s is missing", setting_keys.NOTIFICATIONS_UPDATE_LAST_VERSION)
        return 0

    current_version = current_version or settings.version
    status = get_version_check_status(current_version)
    latest = parse_version(status.latest_version)
    if not status.update_available or latest is None:
        return 0
    last = parse_version(last_raw)
    if last is not None and last >= latest:
        return 0

    admin_ids = [
        user_id for (user_id,) in (
            db.query(User.id)
            .filter(User.is_superuser == True, User.is_active == True)  # noqa: E712
            .all()
        )
    ]
    notified = service.notify(
        "admin_update_available",
        admin_ids,
        data={"version": status.latest_version, "current_version": current_version},
        group_key="admin:update",
    )
    settings_service.update(setting_keys.NOTIFICATIONS_UPDATE_LAST_VERSION, status.latest_version)
    logger.info("Told %s admins about Parker %s", notified, status.latest_version)
    return notified


def cleanup_old_notifications(db: Session, today: date) -> int:
    return NotificationService(db).cleanup_old_notifications()


# --- Hooks ---

ScanProducer = Callable[[Session, ScanInfo], Any]
StartupProducer = Callable[[Session], Any]
DailyCheck = Callable[[Session, date], Any]

SCAN_PRODUCERS: list[ScanProducer] = [
    notify_follow_arrivals,
]

STARTUP_PRODUCERS: list[StartupProducer] = [
    announce_whats_new,
]

DAILY_CHECKS: list[DailyCheck] = [
    announce_year_in_review,
    announce_update_available,
    cleanup_old_notifications,
]


def _run_each(hook_name: str, funcs: list[Callable[..., Any]], db: Session, *args: Any) -> None:
    for func in funcs:
        try:
            func(db, *args)
        except Exception:
            db.rollback()
            logger.exception("Notification %s step %s failed", hook_name, func.__name__)


def run_scan_producers(db: Session, scan: ScanInfo) -> None:
    _run_each("scan", SCAN_PRODUCERS, db, scan)


def run_startup_producers(db: Session) -> None:
    _run_each("startup", STARTUP_PRODUCERS, db)


def run_notification_checks(db: Session, today: Optional[date] = None) -> None:
    _run_each("daily check", DAILY_CHECKS, db, today or date.today())
