from datetime import date

from fastapi import APIRouter, HTTPException
from sqlalchemy import or_

from app.api.deps import AdminUser, SessionDep
from app.models.promoted_rail import PromotedRail
from app.models.user import User
from app.schemas.promoted_rail import (
    PromotedRailCreate,
    PromotedRailQueryUpdate,
    PromotedRailResponse,
    PromotedRailUpdate,
)

router = APIRouter()


def is_promoted_rail_active(rail: PromotedRail, on_date: date | None = None) -> bool:
    current_date = on_date or date.today()
    return bool(
        rail.enabled
        and rail.starts_on <= current_date
        and (rail.ends_on is None or rail.ends_on >= current_date)
    )


def get_active_promoted_rails(db: SessionDep, on_date: date | None = None) -> list[PromotedRail]:
    """Return enabled rails in their admin-defined order for the current calendar date."""
    current_date = on_date or date.today()
    return (
        db.query(PromotedRail)
        .filter(
            PromotedRail.enabled.is_(True),
            PromotedRail.starts_on <= current_date,
            or_(PromotedRail.ends_on.is_(None), PromotedRail.ends_on >= current_date),
        )
        .order_by(PromotedRail.display_order.asc(), PromotedRail.id.asc())
        .all()
    )


def _get_promoted_rail_or_404(db: SessionDep, rail_id: int) -> PromotedRail:
    rail = db.get(PromotedRail, rail_id)
    if rail is None:
        raise HTTPException(status_code=404, detail="Promoted rail not found")
    return rail


def _serialize_promoted_rail(rail: PromotedRail) -> PromotedRailResponse:
    return PromotedRailResponse(
        id=rail.id,
        title=rail.title,
        query=rail.query_config or {},
        starts_on=rail.starts_on,
        ends_on=rail.ends_on,
        enabled=rail.enabled,
        display_order=rail.display_order,
        active=is_promoted_rail_active(rail),
        created_at=rail.created_at,
        updated_at=rail.updated_at,
    )


def _remove_deleted_rail_from_home_layouts(db: SessionDep, rail_id: int) -> None:
    """Avoid retaining a stale hide preference if a database ever reuses an ID."""
    preference_key = str(rail_id)
    users = db.query(User).filter(User.home_rail_layout.isnot(None)).all()
    for user in users:
        layout = user.home_rail_layout
        if not isinstance(layout, dict):
            continue

        visibility = layout.get("promoted_visibility")
        if not isinstance(visibility, dict) or preference_key not in visibility:
            continue

        updated_visibility = visibility.copy()
        updated_visibility.pop(preference_key)
        updated_layout = layout.copy()
        if updated_visibility:
            updated_layout["promoted_visibility"] = updated_visibility
        else:
            updated_layout.pop("promoted_visibility", None)
        user.home_rail_layout = updated_layout


@router.get("/", response_model=list[PromotedRailResponse], tags=["admin"], name="list")
def list_promoted_rails(db: SessionDep, admin_user: AdminUser):
    rails = (
        db.query(PromotedRail)
        .order_by(PromotedRail.starts_on.desc(), PromotedRail.display_order.asc(), PromotedRail.id.asc())
        .all()
    )
    return [_serialize_promoted_rail(rail) for rail in rails]


@router.post("/", response_model=PromotedRailResponse, tags=["admin"], name="create")
def create_promoted_rail(
    data: PromotedRailCreate,
    db: SessionDep,
    admin_user: AdminUser,
):
    rail = PromotedRail(
        title=data.title,
        query_config=data.query.model_dump(),
        starts_on=data.starts_on,
        ends_on=data.ends_on,
        enabled=data.enabled,
        display_order=data.display_order,
    )
    db.add(rail)
    db.commit()
    db.refresh(rail)
    return _serialize_promoted_rail(rail)


@router.get("/{rail_id}", response_model=PromotedRailResponse, tags=["admin"], name="detail")
def get_promoted_rail(rail_id: int, db: SessionDep, admin_user: AdminUser):
    return _serialize_promoted_rail(_get_promoted_rail_or_404(db, rail_id))


@router.patch("/{rail_id}", response_model=PromotedRailResponse, tags=["admin"], name="update")
def update_promoted_rail(
    rail_id: int,
    data: PromotedRailUpdate,
    db: SessionDep,
    admin_user: AdminUser,
):
    rail = _get_promoted_rail_or_404(db, rail_id)
    updates = data.model_dump(exclude_unset=True)
    starts_on = updates.get("starts_on", rail.starts_on)
    ends_on = updates.get("ends_on", rail.ends_on)
    if ends_on is not None and ends_on < starts_on:
        raise HTTPException(status_code=422, detail="End date cannot be before start date")

    for field, value in updates.items():
        setattr(rail, field, value)

    db.commit()
    db.refresh(rail)
    return _serialize_promoted_rail(rail)


@router.patch("/{rail_id}/query", response_model=PromotedRailResponse, tags=["admin"], name="update_query")
def update_promoted_rail_query(
    rail_id: int,
    data: PromotedRailQueryUpdate,
    db: SessionDep,
    admin_user: AdminUser,
):
    rail = _get_promoted_rail_or_404(db, rail_id)
    rail.query_config = data.query.model_dump()
    db.commit()
    db.refresh(rail)
    return _serialize_promoted_rail(rail)


@router.delete("/{rail_id}", tags=["admin"], name="delete")
def delete_promoted_rail(rail_id: int, db: SessionDep, admin_user: AdminUser):
    rail = _get_promoted_rail_or_404(db, rail_id)
    _remove_deleted_rail_from_home_layouts(db, rail_id)
    db.delete(rail)
    db.commit()
    return {"message": "Promoted rail deleted"}
