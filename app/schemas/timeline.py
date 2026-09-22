from typing import Literal

from pydantic import BaseModel, Field


TimelineSubjectType = Literal["character", "team"]


class TimelineSuggestion(BaseModel):
    type: TimelineSubjectType
    name: str


class TimelineSubject(BaseModel):
    type: TimelineSubjectType
    name: str


class TimelineReadingListAnnotation(BaseModel):
    id: int
    name: str
    position: float


class TimelineCollectionAnnotation(BaseModel):
    id: int
    name: str


class TimelineComicEntry(BaseModel):
    id: int
    series_id: int
    series: str
    volume: int | None = None
    number: str | None = None
    title: str | None = None
    year: int | None = None
    month: int | None = None
    day: int | None = None
    date_label: str | None = None
    publisher: str | None = None
    imprint: str | None = None
    format: str | None = None
    story_arc: str | None = None
    thumbnail_path: str
    reading_lists: list[TimelineReadingListAnnotation] = Field(default_factory=list)
    collections: list[TimelineCollectionAnnotation] = Field(default_factory=list)


class TimelineSummary(BaseModel):
    total_issues: int
    dated_issues: int
    undated_issues: int
    series_count: int
    story_arc_count: int
    reading_list_count: int
    collection_count: int
    start_year: int | None = None
    end_year: int | None = None
    per_year_limit: int


class TimelineNamedComicMilestone(BaseModel):
    name: str
    comic: TimelineComicEntry


class TimelineContainerComicMilestone(BaseModel):
    id: int
    name: str
    comic: TimelineComicEntry


class TimelineMilestones(BaseModel):
    first_issue: TimelineComicEntry | None = None
    latest_issue: TimelineComicEntry | None = None
    first_series: list[TimelineNamedComicMilestone] = Field(default_factory=list)
    first_story_arcs: list[TimelineNamedComicMilestone] = Field(default_factory=list)
    first_reading_lists: list[TimelineContainerComicMilestone] = Field(default_factory=list)
    first_collections: list[TimelineContainerComicMilestone] = Field(default_factory=list)


class TimelineYearGroup(BaseModel):
    year: int
    issue_count: int
    hidden_count: int
    entries: list[TimelineComicEntry] = Field(default_factory=list)


class TimelineResponse(BaseModel):
    subject: TimelineSubject
    summary: TimelineSummary
    milestones: TimelineMilestones
    years: list[TimelineYearGroup] = Field(default_factory=list)
    undated_entries: list[TimelineComicEntry] = Field(default_factory=list)
    undated_hidden_count: int
