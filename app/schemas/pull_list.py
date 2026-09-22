from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field, field_validator

PULL_LIST_NAME_MAX_LENGTH = 120
PULL_LIST_DESCRIPTION_MAX_LENGTH = 500

# --- Schemas ---
class PullListCreate(BaseModel):
    name: str = Field(min_length=1, max_length=PULL_LIST_NAME_MAX_LENGTH)
    description: Optional[str] = Field(default=None, max_length=PULL_LIST_DESCRIPTION_MAX_LENGTH)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        if not isinstance(value, str):
            return value

        normalized = value.strip()
        if not normalized:
            raise ValueError("Stack name cannot be empty")
        return normalized

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            return value

        normalized = value.strip()
        return normalized or None

class PullListUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=PULL_LIST_NAME_MAX_LENGTH)
    description: Optional[str] = Field(default=None, max_length=PULL_LIST_DESCRIPTION_MAX_LENGTH)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            return value

        normalized = value.strip()
        if not normalized:
            raise ValueError("Stack name cannot be empty")
        return normalized

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            return value

        normalized = value.strip()
        return normalized or None

class AddComicRequest(BaseModel):
    comic_id: int

class ReorderRequest(BaseModel):
    # Accepts a list of Comic IDs in the new desired order
    comic_ids: List[int]

class BatchAddComicRequest(BaseModel):
    comic_ids: List[int]


class PullListResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PullListListItem(PullListResponse):
    comic_count: int


class PullListComic(BaseModel):
    id: int
    item_id: int
    title: str | None = None
    series_name: str
    volume_number: int | None = None
    number: str | None = None
    thumbnail_path: str
    sort_order: int
    read: bool


class PullListMetadataDetails(BaseModel):
    writers: list[str] = Field(default_factory=list)
    pencillers: list[str] = Field(default_factory=list)
    characters: list[str] = Field(default_factory=list)
    teams: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)


class PullListDetailResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    created_at: datetime | None = None
    items: list[PullListComic] = Field(default_factory=list)
    details: PullListMetadataDetails


class PullListMessageResponse(BaseModel):
    message: str


class PullListAddItemResponse(PullListMessageResponse):
    sort_order: int
