import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


COVER_MURAL_NAME_MAX_LENGTH = 120
COVER_MURAL_DESCRIPTION_MAX_LENGTH = 500
COVER_MURAL_MIN_CANVAS_SIZE = 240
COVER_MURAL_MAX_CANVAS_SIZE = 12000
COVER_MURAL_MAX_PIXELS = 36_000_000
COVER_MURAL_MIN_ITEM_SIZE = 24
COVER_MURAL_MAX_ITEM_SIZE = 6000
COVER_MURAL_MIN_GRID_SIZE = 4
COVER_MURAL_MAX_GRID_SIZE = 200
COVER_MURAL_MAX_EXPORT_BLEED = 100
COVER_MURAL_MAX_EXPORT_SPACING = 1000
COVER_MURAL_MAX_EXPORT_SCALE = 8

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _normalize_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        return value

    normalized = value.strip()
    return normalized or None


def _normalize_name(value: str) -> str:
    if not isinstance(value, str):
        return value

    normalized = value.strip()
    if not normalized:
        raise ValueError("Cover mural name cannot be empty")
    return normalized


def _normalize_hex_color(value: str) -> str:
    if not isinstance(value, str):
        return value

    normalized = value.strip()
    if not _HEX_COLOR_RE.match(normalized):
        raise ValueError("Color must be a #RRGGBB hex value")
    return normalized.lower()


class CoverMuralCreate(BaseModel):
    name: str = Field(min_length=1, max_length=COVER_MURAL_NAME_MAX_LENGTH)
    description: Optional[str] = Field(default=None, max_length=COVER_MURAL_DESCRIPTION_MAX_LENGTH)
    canvas_width: int = Field(default=1200, ge=COVER_MURAL_MIN_CANVAS_SIZE, le=COVER_MURAL_MAX_CANVAS_SIZE)
    canvas_height: int = Field(default=900, ge=COVER_MURAL_MIN_CANVAS_SIZE, le=COVER_MURAL_MAX_CANVAS_SIZE)
    grid_size: int = Field(default=24, ge=COVER_MURAL_MIN_GRID_SIZE, le=COVER_MURAL_MAX_GRID_SIZE)
    background_color: str = Field(default="#111827", min_length=7, max_length=7)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _normalize_name(value)

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_text(value)

    @field_validator("background_color", mode="before")
    @classmethod
    def normalize_background_color(cls, value: str) -> str:
        return _normalize_hex_color(value)


class CoverMuralUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=COVER_MURAL_NAME_MAX_LENGTH)
    description: Optional[str] = Field(default=None, max_length=COVER_MURAL_DESCRIPTION_MAX_LENGTH)
    canvas_width: Optional[int] = Field(default=None, ge=COVER_MURAL_MIN_CANVAS_SIZE, le=COVER_MURAL_MAX_CANVAS_SIZE)
    canvas_height: Optional[int] = Field(default=None, ge=COVER_MURAL_MIN_CANVAS_SIZE, le=COVER_MURAL_MAX_CANVAS_SIZE)
    grid_size: Optional[int] = Field(default=None, ge=COVER_MURAL_MIN_GRID_SIZE, le=COVER_MURAL_MAX_GRID_SIZE)
    background_color: Optional[str] = Field(default=None, min_length=7, max_length=7)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _normalize_name(value)

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_text(value)

    @field_validator("background_color", mode="before")
    @classmethod
    def normalize_background_color(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _normalize_hex_color(value)


class CoverMuralItemLayout(BaseModel):
    item_id: int
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=COVER_MURAL_MIN_ITEM_SIZE, le=COVER_MURAL_MAX_ITEM_SIZE)
    height: int = Field(ge=COVER_MURAL_MIN_ITEM_SIZE, le=COVER_MURAL_MAX_ITEM_SIZE)
    rotation: float = Field(default=0, ge=-360, le=360)
    z_index: int = Field(default=0, ge=0)
    fit_mode: Literal["contain", "cover", "stretch"] = "contain"


class CoverMuralLayoutUpdate(CoverMuralUpdate):
    items: list[CoverMuralItemLayout] = Field(default_factory=list)


class BatchAddCoverMuralItemsRequest(BaseModel):
    comic_ids: list[int] = Field(default_factory=list)
