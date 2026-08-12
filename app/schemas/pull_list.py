from pydantic import BaseModel, Field, field_validator
from typing import Optional, List

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
