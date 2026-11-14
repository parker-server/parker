"""
Problem reports: readers tell the admin something is wrong with a comic.

A report is a note, never a fix. It does not change archives or metadata, and
Parker never closes one on its own. The scanner only records that the comic's
file changed, as a hint for the admin.
"""
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.core.comic_helpers import get_comic_age_restriction
from app.models.comic import Comic, Volume
from app.models.problem_report import ProblemReport
from app.models.series import Series
from app.models.user import User
from app.services.notifications import NotificationService
from app.services.settings_service import PROBLEM_REPORTS_ENABLED_SETTING, SettingsService

logger = logging.getLogger(__name__)

# Stored keys and the labels shown in the modal and the admin queue, in display order.
CATEGORIES: dict[str, str] = {
    "scan_quality": "Bad scan quality",
    "missing_pages": "Missing pages",
    "page_order": "Pages out of order or duplicated",
    "broken": "Won't open or broken pages",
    "metadata": "Wrong metadata",
    "wrong_comic": "Wrong comic",
    "other": "Other",
}
# A page number does not help with these, so the modal hides it and it is not stored.
PAGELESS_CATEGORIES = frozenset({"metadata", "wrong_comic"})
# Categories where the reader must say what is wrong.
MESSAGE_REQUIRED_CATEGORIES = frozenset({"other"})
MESSAGE_MAX_LENGTH = 1000

STATUS_OPEN = "open"
STATUS_RESOLVED = "resolved"
STATUS_DISMISSED = "dismissed"
CLOSED_STATUSES = (STATUS_RESOLVED, STATUS_DISMISSED)

# Admin actions and the status each one moves a report to (None keeps the status).
ADMIN_ACTIONS: dict[str, Optional[str]] = {
    "resolve": STATUS_RESOLVED,
    "dismiss": STATUS_DISMISSED,
    "reopen": STATUS_OPEN,
    "note": None,
}

# Closed groups shown on the admin page. Open ones are always shown in full.
CLOSED_QUEUE_LIMIT = 200


class ProblemReportError(ValueError):
    """A report request the reader or admin can fix."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def problem_reports_enabled(db: Session) -> bool:
    # A missing setting counts as enabled so older installs keep working.
    return SettingsService(db).get(PROBLEM_REPORTS_ENABLED_SETTING) is not False


def user_can_report(user: User, db: Session) -> bool:
    """True when the feature is on and the user is allowed to report. Admins always can."""
    if not problem_reports_enabled(db):
        return False
    return bool(user.is_superuser or user.can_report)


def comic_display_name(comic: Comic) -> str:
    series_name = comic.volume.series.name if comic.volume and comic.volume.series else "Unknown series"
    return f"{series_name} #{comic.number}" if comic.number else series_name


def build_comic_label(comic: Comic) -> str:
    """Series, number and path, kept on the report so it still makes sense after the comic is gone."""
    return f"{comic_display_name(comic)} ({comic.relative_path})"


def report_display_name(report: ProblemReport) -> str:
    return comic_display_name(report.comic) if report.comic else report.comic_label


def category_label(key: str) -> str:
    return CATEGORIES.get(key, key)


class ProblemReportService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # --- Readers ---

    def submit(
        self,
        user: User,
        comic: Comic,
        category: str,
        page_index: Optional[int] = None,
        message: Optional[str] = None,
    ) -> tuple[ProblemReport, bool]:
        """
        Create a report, or update the user's open report for this comic and category.

        Returns (report, created). Admins are notified only for new reports.
        """
        if category not in CATEGORIES:
            raise ProblemReportError(f"Unknown category: {category}")

        message = (message or "").strip() or None
        if message and len(message) > MESSAGE_MAX_LENGTH:
            raise ProblemReportError(f"Details must be {MESSAGE_MAX_LENGTH} characters or fewer")
        if category in MESSAGE_REQUIRED_CATEGORIES and not message:
            raise ProblemReportError("Please describe the problem")

        if category in PAGELESS_CATEGORIES:
            page_index = None
        if page_index is not None:
            if page_index < 0 or (comic.page_count and page_index >= comic.page_count):
                raise ProblemReportError("Page is out of range")

        now = _utcnow()
        report = (
            self.db.query(ProblemReport)
            .filter(
                ProblemReport.user_id == user.id,
                ProblemReport.comic_id == comic.id,
                ProblemReport.category == category,
                ProblemReport.status == STATUS_OPEN,
            )
            .first()
        )
        created = report is None
        if created:
            report = ProblemReport(
                comic_id=comic.id,
                comic_label=build_comic_label(comic),
                user_id=user.id,
                category=category,
                status=STATUS_OPEN,
                created_at=now,
            )
            self.db.add(report)
        else:
            # Reporting again after a file change means the change did not fix it.
            report.file_changed_at = None

        report.page_index = page_index
        report.message = message
        report.updated_at = now
        self.db.commit()
        self.db.refresh(report)

        if created:
            self._notify_admins_new(report, comic)
        return report, created

    def _visible_comic_ids(self, user: User):
        query = (
            self.db.query(Comic.id)
            .join(Volume, Volume.id == Comic.volume_id)
            .join(Series, Series.id == Volume.series_id)
        )
        if not user.is_superuser:
            query = query.filter(Series.library_id.in_([library.id for library in user.accessible_libraries]))
        age_filter = get_comic_age_restriction(user)
        if age_filter is not None:
            query = query.filter(age_filter)
        return query

    def list_for_user(self, user: User) -> list[ProblemReport]:
        """The user's own reports, newest first, leaving out comics they can no longer see."""
        query = self._user_reports_query(user.id)
        if not user.is_superuser:
            query = query.filter(or_(
                ProblemReport.comic_id.is_(None),
                ProblemReport.comic_id.in_(self._visible_comic_ids(user)),
            ))
        return query.order_by(ProblemReport.updated_at.desc(), ProblemReport.id.desc()).all()

    def list_for_user_comic(self, user_id: int, comic_id: int) -> list[ProblemReport]:
        return (
            self._user_reports_query(user_id)
            .filter(ProblemReport.comic_id == comic_id)
            .order_by(ProblemReport.updated_at.desc(), ProblemReport.id.desc())
            .all()
        )

    def withdraw(self, user_id: int, report_id: int) -> bool:
        """Delete the user's own open report. Closed reports stay as a record."""
        report = (
            self.db.query(ProblemReport)
            .filter(
                ProblemReport.id == report_id,
                ProblemReport.user_id == user_id,
                ProblemReport.status == STATUS_OPEN,
            )
            .first()
        )
        if report is None:
            return False
        self.db.delete(report)
        self.db.commit()
        return True

    def _user_reports_query(self, user_id: int):
        return (
            self.db.query(ProblemReport)
            .options(joinedload(ProblemReport.comic).joinedload(Comic.volume).joinedload(Volume.series))
            .filter(ProblemReport.user_id == user_id)
        )

    # --- Admins ---

    def exists(self, report_id: int) -> bool:
        return self.db.query(ProblemReport.id).filter(ProblemReport.id == report_id).first() is not None

    def open_count(self) -> int:
        return self.db.query(ProblemReport.id).filter(ProblemReport.status == STATUS_OPEN).count()

    def queue(
        self,
        status: str = STATUS_OPEN,
        category: Optional[str] = None,
        file_changed_only: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Reports grouped by comic, category and status, newest activity first.

        status is "open", "closed" (resolved and dismissed) or one closed status.
        Reports on a removed comic are grouped by the label they kept.
        """
        query = (
            self.db.query(ProblemReport)
            .options(
                joinedload(ProblemReport.comic).joinedload(Comic.volume).joinedload(Volume.series),
                joinedload(ProblemReport.user),
                joinedload(ProblemReport.resolved_by),
            )
        )
        if status == "closed":
            query = query.filter(ProblemReport.status.in_(CLOSED_STATUSES))
        else:
            query = query.filter(ProblemReport.status == status)
        if category:
            query = query.filter(ProblemReport.category == category)
        if file_changed_only:
            query = query.filter(ProblemReport.file_changed_at.isnot(None))

        reports = query.order_by(ProblemReport.updated_at.desc(), ProblemReport.id.desc()).all()

        groups: dict[tuple, list[ProblemReport]] = defaultdict(list)
        for report in reports:
            comic_key = report.comic_id if report.comic_id is not None else f"label:{report.comic_label}"
            groups[(comic_key, report.category, report.status)].append(report)

        # Reports are newest first, so dict order already puts the newest group first.
        result = [self._serialize_group(group) for group in groups.values()]
        if status != STATUS_OPEN:
            result = result[:CLOSED_QUEUE_LIMIT]
        return result

    def _serialize_group(self, reports: list[ProblemReport]) -> dict[str, Any]:
        first = reports[0]
        comic = first.comic
        pages = [report.page_index for report in reports if report.page_index is not None]
        return {
            "key": f"{first.comic_id or first.comic_label}:{first.category}:{first.status}",
            "comic_id": first.comic_id,
            "comic_name": report_display_name(first),
            "comic_label": first.comic_label,
            "comic_removed": comic is None,
            "category": first.category,
            "category_label": category_label(first.category),
            "status": first.status,
            "count": len(reports),
            # Reports are newest first, so this is the newest reported page.
            "page_index": pages[0] if pages else None,
            "file_changed": any(report.file_changed_at is not None for report in reports),
            "created_at": min(report.created_at for report in reports),
            "updated_at": first.updated_at,
            "report_ids": [report.id for report in reports],
            "reports": [serialize_report(report, include_reporter=True) for report in reports],
        }

    def apply(
        self,
        report_ids: Iterable[int],
        action: str,
        admin: User,
        note: Optional[str] = None,
    ) -> list[ProblemReport]:
        """
        Resolve, dismiss, reopen or add a note to reports. Returns the reports changed.

        A note given with resolve or dismiss is shown to the reporter. Reporters
        are told when their open reports are resolved or dismissed.
        """
        if action not in ADMIN_ACTIONS:
            raise ProblemReportError(f"Unknown action: {action}")
        ids = set(report_ids)
        if not ids:
            return []

        note = (note or "").strip() or None
        if note and len(note) > MESSAGE_MAX_LENGTH:
            raise ProblemReportError(f"Notes must be {MESSAGE_MAX_LENGTH} characters or fewer")

        reports = (
            self.db.query(ProblemReport)
            .options(joinedload(ProblemReport.comic).joinedload(Comic.volume).joinedload(Volume.series))
            .filter(ProblemReport.id.in_(ids))
            .all()
        )
        target_status = ADMIN_ACTIONS[action]
        now = _utcnow()
        changed: list[ProblemReport] = []
        newly_closed: list[ProblemReport] = []

        for report in reports:
            if action == "note":
                report.admin_note = note
            elif action == "reopen":
                if report.status == STATUS_OPEN:
                    continue
                report.status = STATUS_OPEN
                report.resolved_at = None
                report.resolved_by_id = None
                # The old file-change hint is about a fix that did not work.
                report.file_changed_at = None
            else:
                if report.status == target_status and note is None:
                    continue
                if report.status == STATUS_OPEN:
                    newly_closed.append(report)
                report.status = target_status
                report.resolved_at = now
                report.resolved_by_id = admin.id
                if note is not None:
                    report.admin_note = note
            report.updated_at = now
            changed.append(report)

        if changed:
            self.db.commit()
        if newly_closed:
            self._notify_reporters_closed(newly_closed, admin)
        return changed

    # --- Scanner ---

    def mark_file_changed(self, comic_ids: Iterable[int]) -> int:
        """Flag open reports on comics whose file a scan just updated."""
        ids = list(set(comic_ids))
        if not ids:
            return 0
        updated = 0
        now = _utcnow()
        # Stay well under SQLite's bound-parameter limit on very large rescans.
        for start in range(0, len(ids), 500):
            updated += (
                self.db.query(ProblemReport)
                .filter(
                    ProblemReport.comic_id.in_(ids[start:start + 500]),
                    ProblemReport.status == STATUS_OPEN,
                )
                .update({ProblemReport.file_changed_at: now}, synchronize_session=False)
            )
        self.db.commit()
        if updated:
            logger.info("Flagged %s open problem reports whose comic file changed", updated)
        return updated

    # --- Notifications ---

    def _notify_admins_new(self, report: ProblemReport, comic: Comic) -> None:
        admin_ids = [
            user_id for (user_id,) in (
                self.db.query(User.id)
                .filter(User.is_superuser == True, User.is_active == True)  # noqa: E712
                .filter(User.id != report.user_id)
                .all()
            )
        ]
        NotificationService(self.db).notify(
            "admin_problem_new",
            admin_ids,
            data={
                "comic_name": comic_display_name(comic),
                "category_label": category_label(report.category),
            },
            group_key="admin:problems",
        )

    def _notify_reporters_closed(self, reports: list[ProblemReport], admin: User) -> None:
        by_user: dict[int, list[ProblemReport]] = defaultdict(list)
        for report in reports:
            # Skip the acting admin, and reports whose reporter's account was deleted.
            if report.user_id is not None and report.user_id != admin.id:
                by_user[report.user_id].append(report)

        service = NotificationService(self.db)
        for user_id, user_reports in by_user.items():
            report = user_reports[0]
            service.notify(
                "problem_resolved",
                [user_id],
                data={
                    "comic_name": report_display_name(report),
                    "status": report.status,
                    "note": report.admin_note,
                },
                group_key="problem_resolved",
                count=len(user_reports),
            )


def serialize_report(report: ProblemReport, include_reporter: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": report.id,
        "comic_id": report.comic_id,
        "comic_name": report_display_name(report),
        "comic_label": report.comic_label,
        "comic_removed": report.comic_id is None,
        "category": report.category,
        "category_label": category_label(report.category),
        "page_index": report.page_index,
        "message": report.message,
        "status": report.status,
        "admin_note": report.admin_note,
        "file_changed_at": report.file_changed_at,
        "created_at": report.created_at,
        "updated_at": report.updated_at,
        "resolved_at": report.resolved_at,
    }
    if include_reporter:
        data["reporter"] = report.user.username if report.user else None
        data["resolved_by"] = report.resolved_by.username if report.resolved_by else None
    return data


def categories_payload() -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "label": label,
            "uses_page": key not in PAGELESS_CATEGORIES,
            "requires_message": key in MESSAGE_REQUIRED_CATEGORIES,
        }
        for key, label in CATEGORIES.items()
    ]
