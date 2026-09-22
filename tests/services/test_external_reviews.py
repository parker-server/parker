from datetime import date, datetime, timedelta, timezone

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
    STATUS_MATCHED,
    STATUS_NO_REVIEWS,
    _build_query,
)
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
