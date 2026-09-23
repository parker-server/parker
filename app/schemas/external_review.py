from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


ExternalReviewStatus = Literal[
    "disabled",
    "pending",
    "matched",
    "no_reviews",
    "no_match",
    "ambiguous",
    "failed",
]


class ExternalReviewItem(BaseModel):
    id: int
    source_name: str
    author: str | None = None
    score: float | None = None
    review_date: date | None = None
    excerpt: str | None = None
    full_review_url: str | None = None


class ExternalReviewsResponse(BaseModel):
    enabled: bool
    provider: str = "comicbookroundup"
    provider_name: str = "ComicBookRoundup"
    status: ExternalReviewStatus
    source_issue_url: str | None = None
    confidence_score: float | None = None
    last_checked_at: datetime | None = None
    next_retry_at: datetime | None = None
    is_stale: bool = False
    reviews: list[ExternalReviewItem] = Field(default_factory=list)
