from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ComicDetailBookmark(BaseModel):
    id: int
    page_index: int
    label: str | None = None


class ComicDetailResponse(BaseModel):
    id: int
    filename: str
    file_path: str | None = None
    file_size: int | None = None
    thumbnail_hash: int

    library_id: int
    library_name: str

    series_id: int
    series: str
    volume: int | None = None
    number: str | None = None
    title: str | None = None
    summary: str | None = None
    web: str | None = None
    web_label: str | None = None
    web_title: str | None = None
    notes: str | None = None

    year: int | None = None
    month: int | None = None
    day: int | None = None

    credits: dict[str, list[str]] = Field(default_factory=dict)

    publisher: str | None = None
    imprint: str | None = None
    format: str | None = None
    series_group: str | None = None

    page_count: int | None = None
    read_time: str
    scan_information: str | None = None

    age_rating: str | None = None
    language_iso: str | None = None
    community_rating: float | None = None
    source_rating: float | None = None

    characters: list[str] = Field(default_factory=list)
    teams: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)

    alternate_series: str | None = None
    alternate_number: str | None = None
    story_arc: str | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    read_status: Literal["new", "in_progress", "completed"]
    resume_page: int | None = None
    stack_membership_count: int
    bookmarks: list[ComicDetailBookmark] = Field(default_factory=list)

    color_palette: dict[str, Any] | None = None

    parker_rating_average: float | None = None
    parker_rating_count: int = 0
    user_rating: int | None = None
    parker_readers_count: int | None = None
