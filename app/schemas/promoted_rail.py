from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.search import SearchRequest


def _normalize_title(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Title cannot be empty")
    return normalized


def validate_promoted_rail_query(query: SearchRequest) -> SearchRequest:
    """Reject viewer-specific filters that cannot define an instance-wide promotion."""
    if any(search_filter.field == "pull_list" for search_filter in query.filters):
        raise ValueError("Promoted rails cannot use Stack filters")
    return query


class PromotedRailCreate(BaseModel):
    title: str = Field(max_length=120)
    starts_on: date
    ends_on: date | None = None
    enabled: bool = False
    display_order: int = Field(default=0, ge=0)
    query: SearchRequest = Field(default_factory=SearchRequest)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        return _normalize_title(value)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: SearchRequest) -> SearchRequest:
        return validate_promoted_rail_query(value)

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.ends_on is not None and self.ends_on < self.starts_on:
            raise ValueError("End date cannot be before start date")
        return self


class PromotedRailUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    starts_on: date | None = None
    ends_on: date | None = None
    enabled: bool | None = None
    display_order: int | None = Field(default=None, ge=0)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        return _normalize_title(value) if value is not None else value


class PromotedRailQueryUpdate(BaseModel):
    query: SearchRequest

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: SearchRequest) -> SearchRequest:
        return validate_promoted_rail_query(value)


class PromotedRailResponse(BaseModel):
    id: int
    title: str
    query: SearchRequest
    starts_on: date
    ends_on: date | None
    enabled: bool
    display_order: int
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
