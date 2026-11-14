import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.user import User
from app.services.notifications.kinds import KINDS, NotificationKind, get_kind
from app.services.settings_service import SettingsService

logger = logging.getLogger(__name__)

NOTIFICATIONS_ENABLED_SETTING = "features.notifications_enabled"
RETENTION_DAYS_SETTING = "notifications.retention_days"
DEFAULT_RETENTION_DAYS = 30
UNREAD_RETENTION_DAYS = 90
IN_APP_CHANNEL = "in_app"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class NotificationService:
    """
    The only code that reads or writes the notifications table.

    Producers call notify() after their own work has committed. notify() is
    best-effort: failures are logged and never raised into the caller, except
    for an unknown kind, which is a programming error.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- Settings ---

    def is_enabled(self) -> bool:
        value = SettingsService(self.db).get(NOTIFICATIONS_ENABLED_SETTING)
        return True if value is None else bool(value)

    def feature_enabled(self, definition: NotificationKind) -> bool:
        """False when the kind belongs to a feature that is turned off."""
        if definition.feature_setting is None:
            return True
        return SettingsService(self.db).get(definition.feature_setting) is not False

    # --- Writing ---

    def notify(
        self,
        kind: str,
        user_ids: Iterable[int],
        data: dict[str, Any],
        group_key: Optional[str] = None,
        count: int = 1,
    ) -> int:
        """
        Notify users about something. Returns the number of users notified.

        With a group_key, a user's unread notification with the same kind and key
        is updated (count added, text rendered again) instead of adding a row.
        """
        definition = get_kind(kind)
        requested_ids = set(user_ids)
        if not requested_ids or not self.is_enabled() or not self.feature_enabled(definition):
            return 0

        try:
            recipients = self._recipients(definition, requested_ids)
            if not recipients:
                return 0

            existing: dict[int, Notification] = {}
            if group_key:
                rows = (
                    self.db.query(Notification)
                    .filter(
                        Notification.user_id.in_(recipients),
                        Notification.kind == kind,
                        Notification.group_key == group_key,
                        Notification.read_at.is_(None),
                    )
                    .order_by(Notification.updated_at.asc())
                    .all()
                )
                # If there are several, the newest wins.
                existing = {row.user_id: row for row in rows}

            now = _utcnow()
            link = definition.link(data)
            title, body = definition.render(count, data)

            for user_id in recipients:
                row = existing.get(user_id)
                if row is not None:
                    row.count += count
                    row.data = data
                    row.title, row.body = definition.render(row.count, data)
                    row.link = link
                    row.updated_at = now
                    continue

                self.db.add(Notification(
                    user_id=user_id,
                    kind=kind,
                    title=title,
                    body=body,
                    link=link,
                    group_key=group_key,
                    count=count,
                    data=data,
                    created_at=now,
                    updated_at=now,
                ))

            self.db.commit()
            return len(recipients)
        except Exception:
            self.db.rollback()
            logger.exception("Failed to write %s notifications", kind)
            return 0

    def _recipients(self, definition: NotificationKind, user_ids: set[int]) -> list[int]:
        users = (
            self.db.query(User.id, User.is_superuser, User.notification_preferences)
            .filter(User.id.in_(user_ids), User.is_active == True)  # noqa: E712
            .all()
        )
        return [
            user.id
            for user in users
            if (user.is_superuser or not definition.admin_only)
            and self._wants(user.notification_preferences, definition)
        ]

    @staticmethod
    def _wants(
        preferences: Any,
        definition: NotificationKind,
        channel: str = IN_APP_CHANNEL,
    ) -> bool:
        if not isinstance(preferences, dict):
            return definition.default_enabled
        kind_preferences = preferences.get(definition.key)
        if not isinstance(kind_preferences, dict):
            return definition.default_enabled
        value = kind_preferences.get(channel)
        return value if isinstance(value, bool) else definition.default_enabled

    # --- Reading ---

    def _user_query(self, user_id: int):
        return self.db.query(Notification).filter(Notification.user_id == user_id)

    def unread_count(self, user_id: int) -> int:
        return self._user_query(user_id).filter(Notification.read_at.is_(None)).count()

    def list_for_user(
        self,
        user_id: int,
        skip: int = 0,
        limit: int = 20,
        unread_only: bool = False,
    ) -> tuple[int, list[Notification]]:
        query = self._user_query(user_id)
        if unread_only:
            query = query.filter(Notification.read_at.is_(None))

        total = query.count()
        rows = (
            query.order_by(Notification.updated_at.desc(), Notification.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )
        return total, rows

    def mark_read(self, user_id: int, notification_id: int) -> bool:
        row = self._user_query(user_id).filter(Notification.id == notification_id).first()
        if row is None:
            return False
        if row.read_at is None:
            row.read_at = _utcnow()
            self.db.commit()
        return True

    def mark_all_read(self, user_id: int) -> int:
        updated = (
            self._user_query(user_id)
            .filter(Notification.read_at.is_(None))
            .update({Notification.read_at: _utcnow()}, synchronize_session=False)
        )
        self.db.commit()
        return updated

    def delete(self, user_id: int, notification_id: int) -> bool:
        deleted = (
            self._user_query(user_id)
            .filter(Notification.id == notification_id)
            .delete(synchronize_session=False)
        )
        self.db.commit()
        return deleted > 0

    def clear_read(self, user_id: int) -> int:
        deleted = (
            self._user_query(user_id)
            .filter(Notification.read_at.isnot(None))
            .delete(synchronize_session=False)
        )
        self.db.commit()
        return deleted

    # --- Preferences ---

    def available_kinds(self, user: User) -> list[NotificationKind]:
        return [
            kind for kind in KINDS.values()
            if (user.is_superuser or not kind.admin_only) and self.feature_enabled(kind)
        ]

    def preferences_for(self, user: User) -> list[dict[str, Any]]:
        return [
            {
                "key": kind.key,
                "label": kind.label,
                "description": kind.description,
                "icon": kind.icon,
                "enabled": self._wants(user.notification_preferences, kind),
            }
            for kind in self.available_kinds(user)
        ]

    def update_preferences(self, user: User, changes: dict[str, bool]) -> None:
        """Turn kinds on or off for the in-app channel. Raises ValueError for unknown kinds."""
        allowed = {kind.key for kind in self.available_kinds(user)}
        unknown = sorted(set(changes) - allowed)
        if unknown:
            raise ValueError(f"Unknown notification kinds: {', '.join(unknown)}")

        current = user.notification_preferences if isinstance(user.notification_preferences, dict) else {}
        # Build a new dict so SQLAlchemy sees the JSON change.
        updated = {key: dict(value) for key, value in current.items() if isinstance(value, dict)}
        for key, enabled in changes.items():
            updated.setdefault(key, {})[IN_APP_CHANNEL] = bool(enabled)

        user.notification_preferences = updated
        self.db.add(user)
        self.db.commit()

    # --- Retention ---

    def cleanup_old_notifications(self, now: Optional[datetime] = None) -> int:
        """
        Delete read notifications older than the retention setting, and unread
        ones older than UNREAD_RETENTION_DAYS. Runs even when notifications are off.
        """
        now = now or _utcnow()
        retention_days = SettingsService(self.db).get(RETENTION_DAYS_SETTING)
        retention_days = max(1, int(retention_days or DEFAULT_RETENTION_DAYS))

        read_cutoff = now - timedelta(days=retention_days)
        unread_cutoff = now - timedelta(days=UNREAD_RETENTION_DAYS)

        deleted = (
            self.db.query(Notification)
            .filter(Notification.read_at.isnot(None), Notification.updated_at < read_cutoff)
            .delete(synchronize_session=False)
        )
        deleted += (
            self.db.query(Notification)
            .filter(Notification.read_at.is_(None), Notification.updated_at < unread_cutoff)
            .delete(synchronize_session=False)
        )
        self.db.commit()

        if deleted:
            logger.info("Deleted %s old notifications", deleted)
        return deleted
