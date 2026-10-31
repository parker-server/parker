from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import CurrentUser, PaginationParams, SessionDep
from app.models.notification import Notification
from app.services.notifications import KINDS, NotificationService
from app.services.notifications.kinds import FALLBACK_ICON
from app.services.notifications.producers import whats_new_for_user

router = APIRouter()
whats_new_router = APIRouter()


class NotificationPreferencesUpdate(BaseModel):
    kinds: dict[str, bool]


def ensure_notifications_enabled(db: SessionDep) -> None:
    if not NotificationService(db).is_enabled():
        raise HTTPException(status_code=404, detail="Notifications are turned off")


EnabledDep = Depends(ensure_notifications_enabled)


def _serialize(row: Notification) -> dict[str, Any]:
    kind = KINDS.get(row.kind)
    return {
        "id": row.id,
        "kind": row.kind,
        "label": kind.label if kind else row.kind,
        "icon": kind.icon if kind else FALLBACK_ICON,
        "title": row.title,
        "body": row.body,
        "link": row.link,
        "count": row.count,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "read_at": row.read_at,
        "is_read": row.read_at is not None,
    }


@router.get("/unread-count", name="unread_count", dependencies=[EnabledDep])
def get_unread_count(db: SessionDep, current_user: CurrentUser):
    return {"count": NotificationService(db).unread_count(current_user.id)}


@router.get("/", name="list", dependencies=[EnabledDep])
def list_notifications(
    db: SessionDep,
    current_user: CurrentUser,
    params: PaginationParams = Depends(),
    unread_only: bool = Query(False),
):
    service = NotificationService(db)
    total, rows = service.list_for_user(
        current_user.id,
        skip=params.skip,
        limit=params.size,
        unread_only=unread_only,
    )
    return {
        "total": total,
        "page": params.page,
        "size": params.size,
        "unread_count": service.unread_count(current_user.id),
        "items": [_serialize(row) for row in rows],
    }


@router.post("/read-all", name="mark_all_read", dependencies=[EnabledDep])
def mark_all_read(db: SessionDep, current_user: CurrentUser):
    return {"updated": NotificationService(db).mark_all_read(current_user.id)}


@router.post("/{notification_id}/read", name="mark_read", dependencies=[EnabledDep])
def mark_read(notification_id: int, db: SessionDep, current_user: CurrentUser):
    if not NotificationService(db).mark_read(current_user.id, notification_id):
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "success"}


@router.delete("/{notification_id}", name="delete", dependencies=[EnabledDep])
def delete_notification(notification_id: int, db: SessionDep, current_user: CurrentUser):
    if not NotificationService(db).delete(current_user.id, notification_id):
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "success"}


@router.delete("/", name="clear_read", dependencies=[EnabledDep])
def clear_read(db: SessionDep, current_user: CurrentUser):
    return {"deleted": NotificationService(db).clear_read(current_user.id)}


@router.get("/preferences", name="preferences", dependencies=[EnabledDep])
def get_preferences(db: SessionDep, current_user: CurrentUser):
    return {"kinds": NotificationService(db).preferences_for(current_user)}


@router.put("/preferences", name="update_preferences", dependencies=[EnabledDep])
def update_preferences(
    payload: NotificationPreferencesUpdate,
    db: SessionDep,
    current_user: CurrentUser,
):
    service = NotificationService(db)
    try:
        service.update_preferences(current_user, payload.kinds)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"kinds": service.preferences_for(current_user)}


@whats_new_router.get("/", name="list")
def list_whats_new(current_user: CurrentUser):
    """What's New entries for the current user, newest version first."""
    return [
        {
            "version": entry.version,
            "title": entry.title,
            "body": entry.body,
            "link": entry.link,
            "audience": entry.audience,
        }
        for entry in whats_new_for_user(is_admin=bool(current_user.is_superuser))
    ]
