"""
Built-in notification producers and the hooks that run them.

A producer decides who should hear about something and passes the data the
kind needs to NotificationService.notify(). Hooks run a list of producers and
keep one failing producer from stopping the others or the caller.
"""
import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.library import Library
from app.models.user import User
from app.services.notifications.service import NotificationService
from app.services.settings_service import SettingsService
from app.services.version_check import parse_version

logger = logging.getLogger(__name__)

WHATS_NEW_FILE = Path(__file__).resolve().parents[2] / "data" / "whats_new.json"
WHATS_NEW_LAST_VERSION_SETTING = "notifications.whats_new_last_version"
WHATS_NEW_AUDIENCES = ("all", "admin")

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
    last_raw = settings_service.get(WHATS_NEW_LAST_VERSION_SETTING)
    if last_raw is None:
        logger.warning("What's New skipped: %s is missing", WHATS_NEW_LAST_VERSION_SETTING)
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

    settings_service.update(WHATS_NEW_LAST_VERSION_SETTING, current_label)
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


# --- Daily checks ---

def cleanup_old_notifications(db: Session, today: date) -> int:
    return NotificationService(db).cleanup_old_notifications()


# --- Hooks ---

StartupProducer = Callable[[Session], Any]
DailyCheck = Callable[[Session, date], Any]

STARTUP_PRODUCERS: list[StartupProducer] = [
    announce_whats_new,
]

DAILY_CHECKS: list[DailyCheck] = [
    cleanup_old_notifications,
]


def _run_each(hook_name: str, funcs: list[Callable[..., Any]], db: Session, *args: Any) -> None:
    for func in funcs:
        try:
            func(db, *args)
        except Exception:
            db.rollback()
            logger.exception("Notification %s step %s failed", hook_name, func.__name__)


def run_startup_producers(db: Session) -> None:
    _run_each("startup", STARTUP_PRODUCERS, db)


def run_notification_checks(db: Session, today: Optional[date] = None) -> None:
    _run_each("daily check", DAILY_CHECKS, db, today or date.today())
