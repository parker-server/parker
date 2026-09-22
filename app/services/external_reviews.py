from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.text_utils import normalize_title
from app.database import SessionLocal
from app.models.comic import Comic, Volume
from app.models.credits import ComicCredit
from app.models.external_review import ExternalReview, ExternalReviewLookup
from app.schemas.external_review import ExternalReviewItem, ExternalReviewsResponse
from app.services.comicbookroundup import ComicBookRoundupClient, ComicBookRoundupIssueQuery

logger = logging.getLogger(__name__)

COMICBOOKROUNDUP_PROVIDER = "comicbookroundup"
EXTERNAL_REVIEWS_ENABLED_SETTING = "integrations.external_reviews.enabled"

STATUS_PENDING = "pending"
STATUS_MATCHED = "matched"
STATUS_NO_REVIEWS = "no_reviews"
STATUS_NO_MATCH = "no_match"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_FAILED = "failed"

MATCH_REFRESH_AFTER = timedelta(days=30)
MATCH_REVIEW_GROWTH_WINDOW = timedelta(days=180)
NEGATIVE_RETRY_AFTER = timedelta(days=14)
FAILED_RETRY_AFTER = timedelta(days=1)
PENDING_RETRY_AFTER = timedelta(minutes=2)
MAX_STORED_CRITIC_REVIEWS = 20
MAX_EXCERPT_LENGTH = 500


class ExternalReviewService:
    def __init__(self, db: Session, client: ComicBookRoundupClient | None = None):
        self.db = db
        self.client = client or ComicBookRoundupClient()

    def get_lookup(self, comic_id: int) -> ExternalReviewLookup | None:
        return (
            self.db.query(ExternalReviewLookup)
            .options(selectinload(ExternalReviewLookup.reviews))
            .filter(
                ExternalReviewLookup.comic_id == comic_id,
                ExternalReviewLookup.provider == COMICBOOKROUNDUP_PROVIDER,
            )
            .first()
        )

    def get_or_create_lookup(self, comic_id: int) -> ExternalReviewLookup:
        lookup = self.get_lookup(comic_id)
        if lookup:
            return lookup

        lookup = ExternalReviewLookup(
            comic_id=comic_id,
            provider=COMICBOOKROUNDUP_PROVIDER,
            status=STATUS_PENDING,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        self.db.add(lookup)
        self.db.flush()
        return lookup

    def should_refresh(self, lookup: ExternalReviewLookup | None, *, force: bool = False) -> bool:
        if lookup is None:
            return True

        now = _utcnow()
        if lookup.status == STATUS_PENDING:
            updated_at = _as_utc(lookup.updated_at)
            return bool(updated_at and now - updated_at >= PENDING_RETRY_AFTER)

        if force:
            return True

        next_retry_at = _as_utc(lookup.next_retry_at)
        if next_retry_at and next_retry_at > now:
            return False

        if lookup.status == STATUS_MATCHED:
            return next_retry_at is not None

        return lookup.status in {STATUS_NO_REVIEWS, STATUS_NO_MATCH, STATUS_AMBIGUOUS, STATUS_FAILED}

    def normalize_lookup_policy(self, lookup: ExternalReviewLookup | None, comic: Comic) -> ExternalReviewLookup | None:
        if lookup is None or lookup.status != STATUS_MATCHED:
            return lookup

        checked_at = _as_utc(lookup.last_checked_at) or _utcnow()
        expected_next_retry_at = _matched_next_retry_at(
            comic,
            checked_at,
            had_previous_match=True,
        )
        current_next_retry_at = _as_utc(lookup.next_retry_at)
        if current_next_retry_at == expected_next_retry_at:
            return lookup

        lookup.next_retry_at = expected_next_retry_at
        lookup.updated_at = _utcnow()
        self.db.commit()
        self.db.refresh(lookup)
        return lookup

    def claim_refresh(self, comic_id: int, *, force: bool = False) -> tuple[ExternalReviewLookup, bool]:
        lookup = self.get_lookup(comic_id)
        if lookup and not self.should_refresh(lookup, force=force):
            return lookup, False

        if lookup is None:
            lookup = ExternalReviewLookup(
                comic_id=comic_id,
                provider=COMICBOOKROUNDUP_PROVIDER,
                status=STATUS_PENDING,
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            self.db.add(lookup)
            try:
                self.db.commit()
                self.db.refresh(lookup)
                return lookup, True
            except IntegrityError:
                self.db.rollback()
                lookup = self.get_lookup(comic_id)
                if lookup is None:
                    raise
                if not self.should_refresh(lookup, force=False):
                    return lookup, False

        lookup.status = STATUS_PENDING
        lookup.error_message = None
        lookup.next_retry_at = None
        lookup.updated_at = _utcnow()
        self.db.commit()
        self.db.refresh(lookup)
        return lookup, True

    def response_for_lookup(
        self,
        lookup: ExternalReviewLookup | None,
        *,
        enabled: bool,
    ) -> ExternalReviewsResponse:
        if not enabled:
            return ExternalReviewsResponse(enabled=False, status="disabled")

        if lookup is None:
            return ExternalReviewsResponse(enabled=True, status=STATUS_PENDING)

        reviews = [
            ExternalReviewItem(
                id=review.id,
                source_name=review.source_name,
                author=review.author,
                score=review.score,
                review_date=review.review_date,
                excerpt=review.excerpt,
                full_review_url=review.full_review_url,
            )
            for review in sorted(lookup.reviews, key=lambda item: item.sort_order)
            if review.review_type == "critic"
        ]

        return ExternalReviewsResponse(
            enabled=True,
            status=lookup.status,
            source_issue_url=lookup.source_issue_url,
            confidence_score=lookup.confidence_score,
            last_checked_at=lookup.last_checked_at,
            next_retry_at=lookup.next_retry_at,
            is_stale=self._is_stale(lookup),
            reviews=reviews,
        )

    def refresh_comic(self, comic: Comic) -> ExternalReviewLookup:
        lookup = self.get_or_create_lookup(comic.id)
        previous_status = lookup.status
        lookup.status = STATUS_PENDING
        lookup.error_message = None
        lookup.updated_at = _utcnow()
        self.db.commit()

        try:
            result = asyncio.run(self.client.lookup_issue(_build_query(comic), max_candidates=10))
            now = _utcnow()
            lookup.last_checked_at = now
            lookup.updated_at = now
            lookup.error_message = None
            lookup.reviews.clear()

            if result.is_confident and result.best_match:
                best_match = result.best_match
                page = best_match.page
                lookup.source_issue_url = page.url
                lookup.confidence_score = best_match.score

                critic_reviews = [
                    review
                    for review in page.reviews
                    if review.review_type == "critic"
                ][:MAX_STORED_CRITIC_REVIEWS]

                if critic_reviews:
                    lookup.status = STATUS_MATCHED
                    lookup.next_retry_at = _matched_next_retry_at(
                        comic,
                        now,
                        had_previous_match=previous_status == STATUS_MATCHED,
                    )

                    for index, review in enumerate(critic_reviews):
                        lookup.reviews.append(
                            ExternalReview(
                                review_type="critic",
                                source_name=review.source,
                                author=review.author,
                                score=review.score,
                                review_date=review.review_date,
                                excerpt=_truncate_excerpt(review.excerpt),
                                full_review_url=review.full_review_url,
                                sort_order=index,
                                created_at=now,
                                updated_at=now,
                            )
                        )
                else:
                    lookup.status = STATUS_NO_REVIEWS
                    lookup.next_retry_at = now + NEGATIVE_RETRY_AFTER
            elif result.matches:
                best_match = result.best_match
                lookup.status = STATUS_AMBIGUOUS
                lookup.source_issue_url = best_match.page.url if best_match else None
                lookup.confidence_score = best_match.score if best_match else None
                lookup.next_retry_at = now + NEGATIVE_RETRY_AFTER
            else:
                lookup.status = STATUS_NO_MATCH
                lookup.source_issue_url = None
                lookup.confidence_score = None
                lookup.next_retry_at = now + NEGATIVE_RETRY_AFTER

            self.db.commit()
            self.db.refresh(lookup)
            return lookup
        except Exception as exc:
            logger.warning("ComicBookRoundup refresh failed for comic %s: %s", comic.id, exc)
            now = _utcnow()
            lookup.status = STATUS_FAILED
            lookup.last_checked_at = now
            lookup.next_retry_at = now + FAILED_RETRY_AFTER
            lookup.error_message = str(exc)[:500]
            lookup.updated_at = now
            self.db.commit()
            self.db.refresh(lookup)
            return lookup

    def _is_stale(self, lookup: ExternalReviewLookup) -> bool:
        next_retry_at = _as_utc(lookup.next_retry_at)
        return bool(next_retry_at and next_retry_at <= _utcnow())


def refresh_external_reviews_for_comic(comic_id: int) -> None:
    db = SessionLocal()
    try:
        comic = (
            db.query(Comic)
            .options(
                joinedload(Comic.volume).joinedload(Volume.series),
                selectinload(Comic.credits).joinedload(ComicCredit.person),
            )
            .filter(Comic.id == comic_id)
            .first()
        )
        if not comic:
            return

        ExternalReviewService(db).refresh_comic(comic)
    finally:
        db.close()


def _build_query(comic: Comic) -> ComicBookRoundupIssueQuery:
    return ComicBookRoundupIssueQuery(
        series_title=comic.volume.series.name,
        issue_number=comic.number,
        issue_title=_issue_title_for_query(comic),
        publisher=comic.publisher,
        release_date=_release_date_for_query(comic),
        writers=tuple(comic.get_credits_by_role("writer")),
        artists=tuple(comic.get_credits_by_role("penciller")),
    )


def _issue_title_for_query(comic: Comic) -> str | None:
    if not comic.title:
        return None

    title = _strip_issue_number_suffix(comic.title, comic.number)
    title_key = normalize_title(title)
    series_key = normalize_title(comic.volume.series.name)
    if not title_key or title_key == series_key:
        return None

    series_prefix_match = re.match(
        rf"^\s*{re.escape(comic.volume.series.name)}\s*[:\-–—]\s*(.+)$",
        title,
        flags=re.IGNORECASE,
    )
    if series_prefix_match:
        title = _strip_issue_number_suffix(series_prefix_match.group(1), comic.number)
        return title or None

    if title_key.startswith(series_key):
        remainder_key = title_key[len(series_key):].strip()
        return remainder_key or None

    return title


def _strip_issue_number_suffix(title: str, issue_number: str | None) -> str:
    title = title.strip()
    if not issue_number:
        return title

    issue_number = issue_number.strip().lstrip("#")
    if not issue_number:
        return title

    return re.sub(rf"\s*#?\s*{re.escape(issue_number)}\s*$", "", title).strip()


def _release_date_for_query(comic: Comic) -> date | None:
    if not comic.year or not comic.month or not comic.day:
        return None
    try:
        return date(int(comic.year), int(comic.month), int(comic.day))
    except ValueError:
        return None


def _matched_next_retry_at(
    comic: Comic,
    checked_at: datetime,
    *,
    had_previous_match: bool,
) -> datetime | None:
    release_date = _release_date_for_query(comic)
    if release_date is None:
        return None if had_previous_match else checked_at + MATCH_REFRESH_AFTER

    release_at = datetime(
        release_date.year,
        release_date.month,
        release_date.day,
        tzinfo=timezone.utc,
    )
    growth_window_ends_at = release_at + MATCH_REVIEW_GROWTH_WINDOW
    if checked_at >= growth_window_ends_at:
        return None

    return min(checked_at + MATCH_REFRESH_AFTER, growth_window_ends_at)


def _truncate_excerpt(value: str | None) -> str | None:
    if not value:
        return None

    cleaned = " ".join(value.split())
    if len(cleaned) <= MAX_EXCERPT_LENGTH:
        return cleaned
    return f"{cleaned[:MAX_EXCERPT_LENGTH - 3].rstrip()}..."


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
