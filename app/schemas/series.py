from typing import Any, Literal

from pydantic import BaseModel, Field


class SeriesResumeTarget(BaseModel):
    comic_id: int | None = None
    status: Literal["new", "in_progress", "continue"]


class SeriesDetailVolume(BaseModel):
    volume_id: int
    volume_number: int | None = None
    first_issue_id: int | None = None
    thumbnail_hash: int | None = None
    issue_count: int
    read: bool


class SeriesDetailContainer(BaseModel):
    id: int
    name: str
    description: str | None = None
    comic_count: int


class SeriesDetailStoryArc(BaseModel):
    name: str
    first_issue_id: int
    count: int


class SeriesDetailResponse(BaseModel):
    id: int
    name: str
    library_id: int
    library_name: str | None = None
    publisher: str | None = None
    imprint: str | None = None
    start_year: int | None = None

    volume_count: int = 0
    total_issues: int = 0
    annual_count: int = 0
    special_count: int = 0
    is_standalone: bool = False
    total_pages: int = 0
    file_size: int = 0
    read_time: str = "0m"
    starred: bool = False

    first_issue_id: int | None = None
    first_issue_summary: str | None = None
    volumes: list[SeriesDetailVolume] = Field(default_factory=list)
    collections: list[SeriesDetailContainer] = Field(default_factory=list)
    reading_lists: list[SeriesDetailContainer] = Field(default_factory=list)
    story_arcs: list[SeriesDetailStoryArc] = Field(default_factory=list)
    resume_to: SeriesResumeTarget | None = None
    colors: dict[str, Any] = Field(default_factory=dict)

    is_admin: bool = False
    is_reverse_numbering: bool = False
    thumbnail_hash: int | None = None
    parker_readers_count: int | None = None
