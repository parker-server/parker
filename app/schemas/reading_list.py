from datetime import datetime

from pydantic import BaseModel, Field


class ReadingListRenameRequest(BaseModel):
    name: str


class ReadingListRenameResponse(BaseModel):
    id: int
    name: str
    source: str
    source_label: str


class ReadingListListItem(BaseModel):
    id: int
    name: str
    description: str | None = None
    source: str
    source_label: str
    comic_count: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ReadingListComic(BaseModel):
    position: float
    id: int
    series_id: int
    series: str
    volume: int | None = None
    number: str | None = None
    title: str | None = None
    summary: str | None = None
    filename: str
    year: int | None = None
    format: str | None = None
    thumbnail_path: str


class ReadingListMetadataDetails(BaseModel):
    writers: list[str] = Field(default_factory=list)
    pencillers: list[str] = Field(default_factory=list)
    characters: list[str] = Field(default_factory=list)
    teams: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)


class ReadingListCBLSource(BaseModel):
    id: int
    origin: str
    last_refresh_status: str
    last_refreshed_at: datetime | None = None


class ReadingListDetailResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    source: str
    source_label: str
    comic_count: int
    comics: list[ReadingListComic] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    details: ReadingListMetadataDetails
    cbl_source: ReadingListCBLSource | None = None
