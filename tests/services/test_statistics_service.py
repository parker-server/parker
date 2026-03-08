from datetime import datetime, time, timedelta, timezone

from app.models.activity_log import ActivityLog
from app.models.comic import Comic, Volume
from app.models.credits import ComicCredit, Person
from app.models.library import Library
from app.models.reading_progress import ReadingProgress
from app.models.series import Series
from app.models.tags import Character, Genre
from app.services.statistics import StatisticsService


def _create_series_volume(db, prefix: str):
    library = Library(name=f"{prefix}-lib", path=f"/tmp/{prefix}-lib")
    series = Series(name=f"{prefix}-series", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([library, series, volume])
    db.flush()
    return series, volume


def _create_comic(db, volume: Volume, slug: str, number: str, page_count: int, *, age_rating: str = "Teen"):
    comic = Comic(
        volume_id=volume.id,
        number=number,
        title=f"{slug}-title",
        filename=f"{slug}.cbz",
        file_path=f"/tmp/{slug}.cbz",
        page_count=page_count,
        publisher="Stat Pub",
        age_rating=age_rating,
    )
    db.add(comic)
    db.flush()
    return comic


def _add_progress(db, user_id: int, comic_id: int, total_pages: int, *, read_at: datetime, created_at: datetime):
    db.add(
        ReadingProgress(
            user_id=user_id,
            comic_id=comic_id,
            current_page=max(total_pages - 1, 0),
            total_pages=total_pages,
            completed=True,
            last_read_at=read_at,
            created_at=created_at,
        )
    )


def _add_activity(db, user_id: int, comic_id: int, pages_read: int, *, at: datetime):
    db.add(
        ActivityLog(
            user_id=user_id,
            comic_id=comic_id,
            pages_read=pages_read,
            start_page=0,
            end_page=pages_read,
            created_at=at,
        )
    )


def _midday(day):
    return datetime.combine(day, time(12, 0), tzinfo=timezone.utc)


def test_year_wrapped_populated_payload(db, normal_user):
    year = 2025

    series_a, volume_a = _create_series_volume(db, "year-a")
    series_b, volume_b = _create_series_volume(db, "year-b")

    comic_a1 = _create_comic(db, volume_a, "year-a1", "1", 200)
    comic_a2 = _create_comic(db, volume_a, "year-a2", "2", 200)
    comic_b1 = _create_comic(db, volume_b, "year-b1", "1", 100)

    writer_a = Person(name="Year Writer A")
    writer_b = Person(name="Year Writer B")
    artist_a = Person(name="Year Artist A")
    artist_b = Person(name="Year Artist B")
    genre_a = Genre(name="Year Genre A")
    genre_b = Genre(name="Year Genre B")
    char_a = Character(name="Year Character A")
    char_b = Character(name="Year Character B")
    db.add_all([writer_a, writer_b, artist_a, artist_b, genre_a, genre_b, char_a, char_b])
    db.flush()

    comic_a1.genres.append(genre_a)
    comic_a2.genres.append(genre_a)
    comic_b1.genres.append(genre_b)

    comic_a1.characters.append(char_a)
    comic_a2.characters.append(char_a)
    comic_b1.characters.append(char_b)

    db.add_all(
        [
            ComicCredit(comic_id=comic_a1.id, person_id=writer_a.id, role="writer"),
            ComicCredit(comic_id=comic_a2.id, person_id=writer_a.id, role="writer"),
            ComicCredit(comic_id=comic_b1.id, person_id=writer_b.id, role="writer"),
            ComicCredit(comic_id=comic_a1.id, person_id=artist_a.id, role="penciller"),
            ComicCredit(comic_id=comic_a2.id, person_id=artist_a.id, role="penciller"),
            ComicCredit(comic_id=comic_b1.id, person_id=artist_b.id, role="penciller"),
        ]
    )

    jan_10 = datetime(year, 1, 10, 10, 0, tzinfo=timezone.utc)
    jan_11 = datetime(year, 1, 11, 11, 0, tzinfo=timezone.utc)
    mar_5 = datetime(year, 3, 5, 12, 0, tzinfo=timezone.utc)

    _add_progress(db, normal_user.id, comic_a1.id, 200, read_at=jan_10, created_at=datetime(year, 1, 1, tzinfo=timezone.utc))
    _add_progress(db, normal_user.id, comic_a2.id, 200, read_at=jan_11, created_at=datetime(year, 1, 2, tzinfo=timezone.utc))
    _add_progress(db, normal_user.id, comic_b1.id, 100, read_at=mar_5, created_at=datetime(year, 3, 1, tzinfo=timezone.utc))

    _add_activity(db, normal_user.id, comic_a1.id, 60, at=jan_10)
    _add_activity(db, normal_user.id, comic_a2.id, 40, at=jan_11)
    _add_activity(db, normal_user.id, comic_b1.id, 20, at=mar_5)

    db.commit()

    payload = StatisticsService(db, normal_user).get_year_wrapped(year)

    assert payload["year"] == year
    assert payload["stats"]["comics_completed"] == 3
    assert payload["stats"]["total_pages"] == 500
    assert payload["stats"]["series_explored"] == 2
    assert payload["stats"]["volumes_completed"] == 2

    assert payload["favorites"]["top_writer"] == {"name": "Year Writer A", "comics_read": 2}
    assert payload["favorites"]["top_artist"] == {"name": "Year Artist A", "comics_read": 2}
    assert payload["favorites"]["top_series"] == {"name": series_a.name, "issues_read": 2}
    assert payload["favorites"]["top_genre"] == {"name": "Year Genre A", "count": 2}
    assert payload["favorites"]["top_character"] == {"name": "Year Character A", "appearances": 2}

    assert payload["highlights"]["busiest_month"] == {"name": "January", "comics_read": 3}
    assert payload["highlights"]["longest_streak"] == 2
    assert payload["highlights"]["longest_series_completed"] == {"name": series_a.name, "issues_completed": 2}

    assert "full days" in payload["fun_facts"]["marathon"]
    assert payload["velocity"] == {
        "active_days": 3,
        "total_pages_year": 120,
        "pages_per_active_day": 40.0,
        "avg_burst": 40.0,
    }


def test_year_wrapped_empty_year_uses_fallbacks_and_age_filter_path(db, normal_user):
    year = 2025
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()
    db.refresh(normal_user)

    payload = StatisticsService(db, normal_user).get_year_wrapped(year)

    assert payload["stats"] == {
        "comics_completed": 0,
        "total_pages": 0,
        "series_explored": 0,
        "volumes_completed": 0,
        "reading_hours": 0.0,
        "graphic_novels_equivalent": 0.0,
        "days_equivalent": 0.0,
    }

    assert payload["favorites"]["top_writer"] == {"name": None, "comics_read": 0}
    assert payload["favorites"]["top_artist"] == {"name": None, "comics_read": 0}
    assert payload["favorites"]["top_series"] == {"name": None, "issues_read": 0}
    assert payload["favorites"]["top_genre"] == {"name": None, "count": 0}
    assert payload["favorites"]["top_character"] == {"name": None, "appearances": 0}

    assert payload["highlights"]["busiest_month"] == {"name": None, "comics_read": 0}
    assert payload["highlights"]["longest_streak"] == 0
    assert payload["highlights"]["longest_series_completed"] == {"name": None, "issues_completed": 0}

    assert payload["fun_facts"]["marathon"] == "That's 0.0 hours of reading!"
    assert payload["velocity"] == {
        "active_days": 1,
        "total_pages_year": 0,
        "pages_per_active_day": 0.0,
        "avg_burst": 0,
    }


def test_active_streak_returns_zero_when_no_activity(db, normal_user):
    assert StatisticsService(db, normal_user).get_active_streak() == 0


def test_active_streak_returns_zero_for_stale_activity(db, normal_user):
    _, volume = _create_series_volume(db, "stale")
    comic = _create_comic(db, volume, "stale-1", "1", 20)

    stale_date = _midday((datetime.now(timezone.utc) - timedelta(days=3)).date())
    _add_activity(db, normal_user.id, comic.id, 10, at=stale_date)
    db.commit()

    assert StatisticsService(db, normal_user).get_active_streak() == 0


def test_active_streak_counts_consecutive_days_until_gap(db, normal_user):
    _, volume = _create_series_volume(db, "streak")
    comic = _create_comic(db, volume, "streak-1", "1", 20)

    today = datetime.now(timezone.utc).date()
    dates = [today, today - timedelta(days=1), today - timedelta(days=2), today - timedelta(days=4)]

    for idx, day in enumerate(dates):
        _add_activity(db, normal_user.id, comic.id, 5 + idx, at=_midday(day))

    db.commit()

    assert StatisticsService(db, normal_user).get_active_streak() == 3
