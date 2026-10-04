from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OPDSTokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    expires_at: Optional[datetime] = None

    @field_validator("name")
    def name_cannot_be_blank(cls, value):
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("Name cannot be empty")
        return normalized

    @field_validator("expires_at")
    def expires_at_must_be_future(cls, value):
        if value is None:
            return value

        normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        if normalized <= datetime.now(timezone.utc):
            raise ValueError("Expiration must be in the future")
        return normalized


class OPDSTokenResponse(BaseModel):
    id: int
    name: str
    token_hint: str
    created_at: datetime
    last_used_at: Optional[datetime]
    expires_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class OPDSTokenCreateResponse(OPDSTokenResponse):
    token: str
