import re
from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import joinedload

from app.api.deps import CurrentUser, SessionDep
from app.core.comic_helpers import assert_user_can_view_comic
from app.models.annotation import Annotation
from app.models.comic import Comic, Volume
from app.services.annotations import AnnotationService

router = APIRouter()

AnnotationKind = Literal["pin", "rectangle"]
DEFAULT_ANNOTATION_COLOR = "#facc15"
HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")


def normalize_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    return value.strip() or None


def normalize_annotation_color(value: Optional[str]) -> str:
    color = (value or DEFAULT_ANNOTATION_COLOR).strip()
    if not HEX_COLOR_PATTERN.match(color):
        raise ValueError("Annotation color must be a 6-digit hex value")
    return color.lower()


def get_normalized_anchor_number(anchor: dict[str, Any], key: str) -> float:
    try:
        value = float(anchor[key])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"Annotation anchor requires {key}") from None

    if value < 0 or value > 1:
        raise ValueError(f"Annotation anchor {key} must be between 0 and 1")

    return value


def normalize_annotation_anchor(kind: AnnotationKind, anchor: dict[str, Any] | None) -> dict[str, float]:
    if not isinstance(anchor, dict):
        raise ValueError("Annotation anchor is required")

    if kind == "pin":
        return {
            "x": get_normalized_anchor_number(anchor, "x"),
            "y": get_normalized_anchor_number(anchor, "y"),
        }

    x = get_normalized_anchor_number(anchor, "x")
    y = get_normalized_anchor_number(anchor, "y")
    width = get_normalized_anchor_number(anchor, "width")
    height = get_normalized_anchor_number(anchor, "height")

    if width <= 0 or height <= 0:
        raise ValueError("Annotation rectangle must have a positive size")

    if x + width > 1 or y + height > 1:
        raise ValueError("Annotation rectangle must fit within the page")

    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
    }


class CreateAnnotationRequest(BaseModel):
    page_index: int = Field(ge=0)
    kind: AnnotationKind
    title: Optional[str] = Field(default=None, max_length=120)
    body: Optional[str] = Field(default=None, max_length=4000)
    color: Optional[str] = None
    anchor: dict[str, Any]

    @field_validator("title", "body")
    @classmethod
    def normalize_text_fields(cls, value: Optional[str]) -> Optional[str]:
        return normalize_optional_text(value)

    @field_validator("color")
    @classmethod
    def validate_color(cls, value: Optional[str]) -> str:
        return normalize_annotation_color(value)

    @model_validator(mode="after")
    def validate_anchor(self) -> "CreateAnnotationRequest":
        self.anchor = normalize_annotation_anchor(self.kind, self.anchor)
        return self


class UpdateAnnotationRequest(BaseModel):
    page_index: Optional[int] = Field(default=None, ge=0)
    kind: Optional[AnnotationKind] = None
    title: Optional[str] = Field(default=None, max_length=120)
    body: Optional[str] = Field(default=None, max_length=4000)
    color: Optional[str] = None
    anchor: Optional[dict[str, Any]] = None

    @field_validator("title", "body")
    @classmethod
    def normalize_text_fields(cls, value: Optional[str]) -> Optional[str]:
        return normalize_optional_text(value)


def get_annotation_service(
    db: SessionDep,
    user: CurrentUser,
) -> AnnotationService:
    return AnnotationService(db, user_id=user.id)


def serialize_annotation(annotation: Annotation) -> dict:
    return {
        "id": annotation.id,
        "comic_id": annotation.comic_id,
        "page_index": annotation.page_index,
        "kind": annotation.kind,
        "title": annotation.title,
        "body": annotation.body,
        "color": annotation.color,
        "anchor": annotation.anchor_json,
        "created_at": annotation.created_at,
        "updated_at": annotation.updated_at,
    }


def ensure_annotation_comic_access(db: SessionDep, current_user: CurrentUser, comic_id: int) -> Comic:
    comic = (
        db.query(Comic)
        .options(joinedload(Comic.volume).joinedload(Volume.series))
        .filter(Comic.id == comic_id)
        .first()
    )

    if not comic:
        raise HTTPException(status_code=404, detail="Comic not found")

    assert_user_can_view_comic(comic, current_user)
    return comic


def ensure_annotation_page_in_range(comic: Comic, page_index: int) -> None:
    if comic.page_count is not None and comic.page_count > 0 and page_index >= comic.page_count:
        raise HTTPException(status_code=422, detail="Annotation page is out of range")


@router.get("/comic/{comic_id}", name="comic_annotations")
async def get_comic_annotations(
    comic_id: int,
    service: Annotated[AnnotationService, Depends(get_annotation_service)],
    db: SessionDep,
    current_user: CurrentUser,
):
    ensure_annotation_comic_access(db, current_user, comic_id)
    return [serialize_annotation(annotation) for annotation in service.list_for_comic(comic_id)]


@router.get("/comic/{comic_id}/page/{page_index}", name="page_annotations")
async def get_page_annotations(
    comic_id: int,
    page_index: int,
    service: Annotated[AnnotationService, Depends(get_annotation_service)],
    db: SessionDep,
    current_user: CurrentUser,
):
    comic = ensure_annotation_comic_access(db, current_user, comic_id)
    if page_index < 0:
        raise HTTPException(status_code=422, detail="Annotation page is out of range")

    ensure_annotation_page_in_range(comic, page_index)
    return [serialize_annotation(annotation) for annotation in service.list_for_page(comic_id, page_index)]


@router.post("/comic/{comic_id}", name="create_comic_annotation")
async def create_comic_annotation(
    comic_id: int,
    request: CreateAnnotationRequest,
    service: Annotated[AnnotationService, Depends(get_annotation_service)],
    db: SessionDep,
    current_user: CurrentUser,
):
    comic = ensure_annotation_comic_access(db, current_user, comic_id)
    ensure_annotation_page_in_range(comic, request.page_index)

    try:
        annotation = service.create_annotation(
            comic_id=comic_id,
            page_index=request.page_index,
            kind=request.kind,
            title=request.title,
            body=request.body,
            color=request.color or DEFAULT_ANNOTATION_COLOR,
            anchor_json=request.anchor,
        )
        db.commit()
        db.refresh(annotation)
        return serialize_annotation(annotation)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))


@router.patch("/{annotation_id}", name="update_annotation")
async def update_annotation(
    annotation_id: int,
    request: UpdateAnnotationRequest,
    service: Annotated[AnnotationService, Depends(get_annotation_service)],
    db: SessionDep,
    current_user: CurrentUser,
):
    annotation = service.get_annotation(annotation_id)
    if not annotation:
        raise HTTPException(status_code=404, detail="Annotation not found")

    comic = ensure_annotation_comic_access(db, current_user, annotation.comic_id)
    updates = request.model_dump(exclude_unset=True)

    if "page_index" in updates:
        ensure_annotation_page_in_range(comic, updates["page_index"])

    if "color" in updates:
        try:
            updates["color"] = normalize_annotation_color(updates["color"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    if "kind" in updates or "anchor" in updates:
        next_kind = updates.get("kind", annotation.kind)
        next_anchor = updates.get("anchor", annotation.anchor_json)
        try:
            updates["anchor_json"] = normalize_annotation_anchor(next_kind, next_anchor)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        updates.pop("anchor", None)

    try:
        updated = service.update_annotation(annotation_id, updates)
        db.commit()
        db.refresh(updated)
        return serialize_annotation(updated)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{annotation_id}", name="delete_annotation")
async def delete_annotation(
    annotation_id: int,
    service: Annotated[AnnotationService, Depends(get_annotation_service)],
    db: SessionDep,
    current_user: CurrentUser,
):
    annotation = service.get_annotation(annotation_id)
    if not annotation:
        raise HTTPException(status_code=404, detail="Annotation not found")

    ensure_annotation_comic_access(db, current_user, annotation.comic_id)

    try:
        deleted = service.delete_annotation(annotation_id)
        db.commit()
        return {
            "annotation_id": deleted.id,
            "message": "Annotation deleted",
        }
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
