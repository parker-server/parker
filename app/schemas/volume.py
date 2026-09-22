from typing import Literal

from pydantic import BaseModel, Field


class VolumeResumeTarget(BaseModel):
    comic_id: int | None = None
    status: Literal["new", "in_progress", "continue"]


class VolumeDetailStoryArc(BaseModel):
    name: str
    first_issue_id: int
    count: int


class VolumeDetailResponse(BaseModel):
    id: int
    volume_number: int | None = None
    series_id: int
    series_name: str
    series_volume_count: int
    library_id: int
    library_name: str

    total_issues: int = 0
    annual_count: int = 0
    special_count: int = 0
    total_pages: int = 0
    read_time: str = "0m"
    file_size: int = 0

    status: Literal["ongoing", "ended"]
    expected_count: int | None = None
    is_completed: bool = False
    missing_issues: list[int] = Field(default_factory=list)
    is_standalone: bool = False

    publisher: str | None = None
    imprint: str | None = None
    start_year: int | None = None
    end_year: int | None = None
    first_issue_id: int | None = None
    first_issue_summary: str | None = None
    story_arcs: list[VolumeDetailStoryArc] = Field(default_factory=list)
    resume_to: VolumeResumeTarget
    is_following: bool = False
    colors: dict[str, str]
    is_reverse_numbering: bool = False
