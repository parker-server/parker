from datetime import datetime

from pydantic import BaseModel, Field


class CollectionListItem(BaseModel):
    id: int
    name: str
    description: str | None = None
    auto_generated: bool
    comic_count: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CollectionComic(BaseModel):
    id: int
    series_id: int
    series: str
    volume: int | None = None
    number: str | None = None
    title: str | None = None
    filename: str
    year: int | None = None
    format: str | None = None
    thumbnail_path: str


class CollectionMetadataDetails(BaseModel):
    writers: list[str] = Field(default_factory=list)
    pencillers: list[str] = Field(default_factory=list)
    characters: list[str] = Field(default_factory=list)
    teams: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)


class CollectionDetailResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    auto_generated: bool
    comic_count: int
    comics: list[CollectionComic] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    details: CollectionMetadataDetails
