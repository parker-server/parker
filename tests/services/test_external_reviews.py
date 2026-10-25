import sqlite3
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

import app.services.external_reviews as external_reviews
from app.models.comic import Volume
from app.models.series import Series
from app.services.comicbookroundup import (
    ComicBookRoundupIssuePage,
    ComicBookRoundupLookupResult,
    ComicBookRoundupMatch,
    ComicBookRoundupReview,
)
from app.services.external_reviews import (
    ExternalReviewService,
    STATUS_FAILED,
    STATUS_MATCHED,
    STATUS_NO_REVIEWS,
    _build_query,
    refresh_external_reviews_for_comic,
)
from tests.async_helpers import run_async
from tests.factories import create_comic, create_library_with_root


class _FakeComicBookRoundupClient:
    async def lookup_issue(self, query, *, max_candidates=10):
        page = ComicBookRoundupIssuePage(
            url="https://comicbookroundup.com/comic-books/reviews/test-publisher/test-series/1",
            title="Test Series #1",
            publisher="Test Publisher",
            reviews=(
                ComicBookRoundupReview(
                    review_type="critic",
                    source="Critic Site",
                    author="Casey Critic",
                    score=9.0,
                    review_date=date(2026, 9, 22),
                    excerpt="Sharp, well-paced, and memorable.",
                    full_review_url="https://critic.example/review",
                ),
                ComicBookRoundupReview(
                    review_type="user",
                    source="reader42",
                    author=None,
                    score=10.0,
                    review_date=date(2026, 9, 22),
                    excerpt="best issue ever",
                ),
            ),
        )
        match = ComicBookRoundupMatch(page=page, score=92.0, reasons=("strong-title",))
        return ComicBookRoundupLookupResult(
            query=query,
            matches=(match,),
            searched_keywords=("Test Series 1",),
            candidate_count=1,
        )


class _FakeNoCriticReviewsClient:
    async def lookup_issue(self, query, *, max_candidates=10):
        page = ComicBookRoundupIssuePage(
            url="https://comicbookroundup.com/comic-books/reviews/test-publisher/test-series/2",
            title="Test Series #2",
            publisher="Test Publisher",
            reviews=(
                ComicBookRoundupReview(
                    review_type="user",
                    source="reader42",
                    author=None,
                    score=8.0,
                    review_date=date(2026, 9, 22),
                    excerpt="Fun issue.",
                ),
            ),
        )
        match = ComicBookRoundupMatch(page=page, score=90.0, reasons=("strong-title",))
        return ComicBookRoundupLookupResult(
            query=query,
            matches=(match,),
            searched_keywords=("Test Series 2",),
            candidate_count=1,
        )


def _create_external_review_comic(db, tmp_path, lib_name: str, **comic_kwargs):
    library = create_library_with_root(db, lib_name, str(tmp_path / lib_name))
    series = Series(name="Test Series", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()
    comic = create_comic(
        db,
        volume,
        library.active_root,
        f"{lib_name}.cbz",
        number="1",
        title="Test Series #1",
        publisher="Test Publisher",
        filename=f"{lib_name}.cbz",
        **comic_kwargs,
    )
    db.commit()
    return comic


def test_external_review_refresh_stores_critic_reviews_only(db, tmp_path):
    comic = _create_external_review_comic(db, tmp_path, "external-review-lib")

    lookup = ExternalReviewService(db, client=_FakeComicBookRoundupClient()).refresh_comic(comic)

    assert lookup.status == STATUS_MATCHED
    assert lookup.source_issue_url == "https://comicbookroundup.com/comic-books/reviews/test-publisher/test-series/1"
    assert lookup.confidence_score == 92.0
    assert len(lookup.reviews) == 1
    assert lookup.reviews[0].review_type == "critic"
    assert lookup.reviews[0].source_name == "Critic Site"
    assert lookup.reviews[0].author == "Casey Critic"
    assert lookup.reviews[0].score == 9.0


def test_external_review_refresh_tolerates_existing_event_loop(db, tmp_path):
    comic = _create_external_review_comic(db, tmp_path, "external-review-loop-lib")
    service = ExternalReviewService(db, client=_FakeComicBookRoundupClient())

    async def _refresh():
        return service.refresh_comic(comic)

    lookup = run_async(_refresh())

    assert lookup.status == STATUS_MATCHED
    assert lookup.reviews[0].source_name == "Critic Site"


def test_external_review_refresh_rechecks_recent_matched_reviews_during_growth_window(db, tmp_path):
    release_date = date.today() - timedelta(days=30)
    comic = _create_external_review_comic(
        db,
        tmp_path,
        "external-review-recent-match-lib",
        year=release_date.year,
        month=release_date.month,
        day=release_date.day,
    )

    service = ExternalReviewService(db, client=_FakeComicBookRoundupClient())
    lookup = service.refresh_comic(comic)

    assert lookup.status == STATUS_MATCHED
    assert lookup.next_retry_at is not None
    assert service.should_refresh(lookup) is False

    lookup.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    assert service.should_refresh(lookup) is True


def test_external_review_refresh_finalizes_old_matched_reviews(db, tmp_path):
    release_date = date.today() - timedelta(days=365)
    comic = _create_external_review_comic(
        db,
        tmp_path,
        "external-review-old-match-lib",
        year=release_date.year,
        month=release_date.month,
        day=release_date.day,
    )

    service = ExternalReviewService(db, client=_FakeComicBookRoundupClient())
    lookup = service.refresh_comic(comic)

    assert lookup.status == STATUS_MATCHED
    assert lookup.next_retry_at is None
    assert service.should_refresh(lookup) is False


def test_external_review_refresh_unknown_release_date_gets_one_matched_followup(db, tmp_path):
    comic = _create_external_review_comic(db, tmp_path, "external-review-unknown-date-match-lib")

    service = ExternalReviewService(db, client=_FakeComicBookRoundupClient())
    lookup = service.refresh_comic(comic)

    assert lookup.status == STATUS_MATCHED
    assert lookup.next_retry_at is not None

    lookup.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    lookup = service.refresh_comic(comic)

    assert lookup.status == STATUS_MATCHED
    assert lookup.next_retry_at is None


def test_external_review_refresh_marks_confident_page_without_critics_as_no_reviews(db, tmp_path):
    library = create_library_with_root(db, "external-review-empty-lib", str(tmp_path / "lib"))
    series = Series(name="Test Series", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()
    comic = create_comic(
        db,
        volume,
        library.active_root,
        "test-series-2.cbz",
        number="2",
        title="Test Series #2",
        publisher="Test Publisher",
        filename="test-series-2.cbz",
    )
    db.commit()

    service = ExternalReviewService(db, client=_FakeNoCriticReviewsClient())
    lookup = service.refresh_comic(comic)

    assert lookup.status == STATUS_NO_REVIEWS
    assert lookup.source_issue_url == "https://comicbookroundup.com/comic-books/reviews/test-publisher/test-series/2"
    assert lookup.confidence_score == 90.0
    assert lookup.reviews == []

    response = service.response_for_lookup(lookup, enabled=True)
    assert response.status == STATUS_NO_REVIEWS
    assert response.reviews == []


def test_external_review_query_extracts_subtitle_from_series_prefixed_title(db, tmp_path):
    library = create_library_with_root(db, "external-review-query-lib", str(tmp_path / "lib"))
    series = Series(name="Avengers", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()
    comic = create_comic(
        db,
        volume,
        library.active_root,
        "avengers-armageddon-4.cbz",
        number="4",
        title="Avengers: Armageddon #4",
        publisher="Marvel Comics",
        filename="avengers-armageddon-4.cbz",
    )
    db.commit()

    query = _build_query(comic)

    assert query.series_title == "Avengers"
    assert query.issue_title == "Armageddon"
    assert query.issue_number == "4"


# --- Write-safety guardrails (see docs/background-job-manager-scope.md, SQLite Contention) ---

def test_external_review_refresh_holds_no_write_open_during_network_lookup(db, tmp_path):
    # A write held open across the network call would block the scan's metadata
    # writer, which fails the whole scan after a 60-second lock wait.
    comic = _create_external_review_comic(db, tmp_path, "external-review-no-write-lib")
    transaction_open_during_lookup = []

    class _CheckingClient(_FakeComicBookRoundupClient):
        async def lookup_issue(self, query, *, max_candidates=10):
            dbapi_connection = db.connection().connection.driver_connection
            transaction_open_during_lookup.append(dbapi_connection.in_transaction)
            return await super().lookup_issue(query, max_candidates=max_candidates)

    lookup = ExternalReviewService(db, client=_CheckingClient()).refresh_comic(comic)

    assert lookup.status == STATUS_MATCHED
    assert transaction_open_during_lookup == [False]


def test_external_review_refresh_rolls_back_failed_save_and_records_failure(db, tmp_path):
    comic = _create_external_review_comic(db, tmp_path, "external-review-rollback-lib")
    engine = db.get_bind()
    raised = []

    def _lock_review_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO external_reviews") and not raised:
            raised.append(statement)
            raise sqlite3.OperationalError("database is locked")

    event.listen(engine, "before_cursor_execute", _lock_review_insert)
    try:
        lookup = ExternalReviewService(db, client=_FakeComicBookRoundupClient()).refresh_comic(comic)
    finally:
        event.remove(engine, "before_cursor_execute", _lock_review_insert)

    assert raised
    assert lookup.status == STATUS_FAILED
    assert "database is locked" in lookup.error_message
    assert lookup.next_retry_at is not None
    # The half-saved result was discarded, not stored alongside the failure
    assert lookup.reviews == []


def test_refresh_external_reviews_for_comic_returns_job_summary(db, tmp_path, monkeypatch):
    comic = _create_external_review_comic(db, tmp_path, "external-review-summary-lib")
    monkeypatch.setattr(
        external_reviews,
        "SessionLocal",
        sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False),
    )
    monkeypatch.setattr(external_reviews, "ComicBookRoundupClient", _FakeComicBookRoundupClient)

    summary = refresh_external_reviews_for_comic(comic.id)

    assert summary == {
        "provider": "comicbookroundup",
        "comic_id": comic.id,
        "issue_title": "Test Series #1",
        "match_status": STATUS_MATCHED,
        "confidence_score": 92.0,
        "review_count": 1,
        "source_issue_url": "https://comicbookroundup.com/comic-books/reviews/test-publisher/test-series/1",
        "error": None,
    }
    assert refresh_external_reviews_for_comic(999999) is None
