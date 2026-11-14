from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.core.comic_helpers import ensure_comic_access
from app.services.problem_reports import (
    CATEGORIES,
    MESSAGE_MAX_LENGTH,
    ProblemReportError,
    ProblemReportService,
    categories_payload,
    problem_reports_enabled,
    serialize_report,
)

# Reader routes are turned off with the feature setting. Admin routes stay
# available so existing reports can still be worked through.
router = APIRouter()
admin_router = APIRouter()

AdminAction = Literal["resolve", "dismiss", "reopen", "note"]


class ProblemReportCreate(BaseModel):
    comic_id: int
    category: str
    page_index: Optional[int] = Field(default=None, ge=0)
    message: Optional[str] = Field(default=None, max_length=MESSAGE_MAX_LENGTH)


class ProblemReportUpdate(BaseModel):
    action: AdminAction
    note: Optional[str] = Field(default=None, max_length=MESSAGE_MAX_LENGTH)


class ProblemReportBatchUpdate(ProblemReportUpdate):
    report_ids: list[int] = Field(min_length=1, max_length=1000)


def ensure_problem_reports_enabled(db: SessionDep) -> None:
    if not problem_reports_enabled(db):
        raise HTTPException(status_code=404, detail="Problem reports are turned off")


EnabledDep = Depends(ensure_problem_reports_enabled)


@router.get("/categories", name="categories", dependencies=[EnabledDep])
def list_categories(current_user: CurrentUser):
    return {"categories": categories_payload(), "message_max_length": MESSAGE_MAX_LENGTH}


@router.post("/", name="create", dependencies=[EnabledDep])
def create_report(payload: ProblemReportCreate, db: SessionDep, current_user: CurrentUser):
    if not (current_user.is_superuser or current_user.can_report):
        raise HTTPException(status_code=403, detail="You are not allowed to report problems")
    if payload.category not in CATEGORIES:
        raise HTTPException(status_code=422, detail="Unknown category")

    comic = ensure_comic_access(db, current_user, payload.comic_id)
    try:
        report, created = ProblemReportService(db).submit(
            current_user,
            comic,
            payload.category,
            page_index=payload.page_index,
            message=payload.message,
        )
    except ProblemReportError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"created": created, "report": serialize_report(report)}


@router.get("/mine", name="mine", dependencies=[EnabledDep])
def list_my_reports(db: SessionDep, current_user: CurrentUser):
    reports = ProblemReportService(db).list_for_user(current_user)
    return {"items": [serialize_report(report) for report in reports]}


@router.get("/comic/{comic_id}", name="comic", dependencies=[EnabledDep])
def list_my_comic_reports(comic_id: int, db: SessionDep, current_user: CurrentUser):
    ensure_comic_access(db, current_user, comic_id)
    reports = ProblemReportService(db).list_for_user_comic(current_user.id, comic_id)
    return {"items": [serialize_report(report) for report in reports]}


@router.delete("/{report_id}", name="withdraw", dependencies=[EnabledDep])
def withdraw_report(report_id: int, db: SessionDep, current_user: CurrentUser):
    if not ProblemReportService(db).withdraw(current_user.id, report_id):
        raise HTTPException(status_code=404, detail="Open report not found")
    return {"status": "success"}


@admin_router.get("/", name="queue")
def get_queue(
    db: SessionDep,
    admin_user: AdminUser,
    status: Literal["open", "closed", "resolved", "dismissed"] = Query("open"),
    category: Optional[str] = Query(None),
    file_changed: bool = Query(False),
):
    if category and category not in CATEGORIES:
        raise HTTPException(status_code=422, detail="Unknown category")
    service = ProblemReportService(db)
    return {
        "groups": service.queue(status=status, category=category, file_changed_only=file_changed),
        "open_count": service.open_count(),
        "categories": categories_payload(),
    }


@admin_router.get("/summary", name="summary")
def get_summary(db: SessionDep, admin_user: AdminUser):
    return {"open_count": ProblemReportService(db).open_count()}


@admin_router.patch("/{report_id}", name="update")
def update_report(report_id: int, payload: ProblemReportUpdate, db: SessionDep, admin_user: AdminUser):
    return _apply(db, admin_user, [report_id], payload, missing_is_404=True)


@admin_router.post("/batch", name="batch")
def batch_update(payload: ProblemReportBatchUpdate, db: SessionDep, admin_user: AdminUser):
    return _apply(db, admin_user, payload.report_ids, payload)


def _apply(
    db: SessionDep,
    admin_user: AdminUser,
    report_ids: list[int],
    payload: ProblemReportUpdate,
    missing_is_404: bool = False,
) -> dict:
    service = ProblemReportService(db)
    if missing_is_404 and not service.exists(report_ids[0]):
        raise HTTPException(status_code=404, detail="Report not found")
    try:
        changed = service.apply(report_ids, payload.action, admin_user, note=payload.note)
    except ProblemReportError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "updated": len(changed),
        "reports": [serialize_report(report, include_reporter=True) for report in changed],
        "open_count": service.open_count(),
    }
