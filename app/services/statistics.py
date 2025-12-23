import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import func, case, and_, or_, not_
from sqlalchemy.orm import Session, selectinload, contains_eager

from app.models.user import User
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.tags import Genre, comic_genres
from app.models.credits import Person, ComicCredit
from app.models.reading_progress import ReadingProgress
from app.models.tags import Character, comic_characters
from app.models.activity_log import ActivityLog
from app.core.comic_helpers import get_reading_time, get_banned_comic_condition, get_series_age_restriction


class StatisticsService:
    def __init__(self, db: Session, user: User):
        self.db = db
        self.user = user
        self.logger = logging.getLogger(__name__)
        # Pre-cache security filters to avoid repeated calls
        self.series_age_filter = get_series_age_restriction(self.user)
        self.banned_condition = get_banned_comic_condition(self.user)

    def get_dashboard_payload(self):

        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)

        stats_query = self.db.query(
            # Basic stats
            func.count(ReadingProgress.id).label('total_progress_records'),
            func.count(case((ReadingProgress.completed == True, 1))).label('completed_comics'),
            func.sum(case((ReadingProgress.completed == True, Comic.page_count), else_=0)).label('total_pages'),
            func.count(func.distinct(Series.id)).label('series_explored'),

            # Last 30 days
            func.count(case(
                (and_(ReadingProgress.completed == True,
                      ReadingProgress.last_read_at >= thirty_days_ago), 1)
            )).label('recent_comics'),
            func.sum(case(
                (and_(ReadingProgress.completed == True,
                      ReadingProgress.last_read_at >= thirty_days_ago),
                 Comic.page_count),
                else_=0
            )).label('recent_pages'),

            # Average completion time
            func.avg(
                case((ReadingProgress.completed == True,
                      func.julianday(ReadingProgress.last_read_at) -
                      func.julianday(ReadingProgress.created_at)))
            ).label('avg_completion_days')
        ).join(Comic, ReadingProgress.comic_id == Comic.id) \
            .join(Volume, Comic.volume_id == Volume.id) \
            .join(Series, Volume.series_id == Series.id) \
            .filter(ReadingProgress.user_id == self.user.id)

        if self.series_age_filter is not None:
            stats_query = stats_query.filter(self.series_age_filter)

        stats = stats_query.first()

        # Calculate derived stats
        issues_read = stats.completed_comics or 0
        total_pages = stats.total_pages or 0
        time_read_str = get_reading_time(total_pages)
        series_explored = stats.series_explored or 0

        # Reading pace classification
        avg_days = stats.avg_completion_days or 0
        reading_pace = (
            'Binge Reader' if avg_days < 1 else
            'Active Reader' if avg_days < 7 else
            'Casual Reader' if avg_days < 30 else
            'Slow Reader'
        )

        # Get top 3 writers and top 3 artists - let SQL limit results
        # NOTE: We don't need the full creator list, so SQL LIMIT is more efficient
        creator_stats = self.db.query(
            Person.name,
            ComicCredit.role,
            func.count(func.distinct(ReadingProgress.comic_id)).label('comics_read')
        ).join(ComicCredit, Person.id == ComicCredit.person_id) \
            .join(Comic, ComicCredit.comic_id == Comic.id) \
            .join(ReadingProgress, Comic.id == ReadingProgress.comic_id) \
            .join(Volume, Comic.volume_id == Volume.id) \
            .join(Series, Volume.series_id == Series.id) \
            .filter(
            ComicCredit.role.in_(['writer', 'penciller']),
            ReadingProgress.user_id == self.user.id,
            ReadingProgress.completed == True
        )

        if self.series_age_filter is not None:
            creator_stats = creator_stats.filter(self.series_age_filter)

        # Group, sort, and limit in SQL (more efficient than fetching all creators)
        creator_stats = creator_stats.group_by(Person.id, Person.name, ComicCredit.role) \
            .order_by(ComicCredit.role, func.count(func.distinct(ReadingProgress.comic_id)).desc()) \
            .all()

        # Split into writers and artists, take top 3 of each
        top_writers = [
            {'name': c.name, 'comics_read': c.comics_read}
            for c in creator_stats if c.role == 'writer'
        ][:3]

        top_artists = [
            {'name': c.name, 'comics_read': c.comics_read}
            for c in creator_stats if c.role == 'penciller'
        ][:3]

        # === TOP PUBLISHERS (Single Query with SQL sorting) ===
        publisher_stats = self.db.query(
            Comic.publisher,
            func.count(func.distinct(ReadingProgress.comic_id)).label('comics_read')
        ).join(ReadingProgress, Comic.id == ReadingProgress.comic_id) \
            .join(Volume, Comic.volume_id == Volume.id) \
            .join(Series, Volume.series_id == Series.id) \
            .filter(
            ReadingProgress.user_id == self.user.id,
            ReadingProgress.completed == True,
            Comic.publisher.isnot(None)
        )

        if self.series_age_filter is not None:
            publisher_stats = publisher_stats.filter(self.series_age_filter)

        # SQL sorts and limits (more efficient - only returns 3 rows)
        top_publishers = [
            {'name': p.publisher, 'comics_read': p.comics_read}
            for p in publisher_stats.group_by(Comic.publisher)
            .order_by(func.count(func.distinct(ReadingProgress.comic_id)).desc())
            .limit(3).all()
        ]

        # NOTE: We need ALL genres to calculate total for percentages
        # So fetching full list and sorting in Python is appropriate here
        genre_stats = self.db.query(
            Genre.name,
            func.count(func.distinct(ReadingProgress.comic_id)).label('count')
        ).join(comic_genres, Genre.id == comic_genres.c.genre_id) \
            .join(Comic, comic_genres.c.comic_id == Comic.id) \
            .join(ReadingProgress, Comic.id == ReadingProgress.comic_id) \
            .join(Volume, Comic.volume_id == Volume.id) \
            .join(Series, Volume.series_id == Series.id) \
            .filter(
            ReadingProgress.user_id == self.user.id,
            ReadingProgress.completed == True
        )

        if self.series_age_filter is not None:
            genre_stats = genre_stats.filter(self.series_age_filter)

        # Fetch all genres (needed for total count and percentage calculation)
        genres = genre_stats.group_by(Genre.id, Genre.name).all()

        # Calculate total and percentages in Python
        total_genre_reads = sum(g.count for g in genres)

        # Sort in Python and take top 5 (dataset is small ~10-30 genres)
        sorted_genres = sorted(genres, key=lambda x: x.count, reverse=True)[:5]

        genre_diversity = {
            'genres_explored': len(genres),
            'top_genres': [
                {
                    'name': g.name,
                    'count': g.count,
                    'percentage': round((g.count / total_genre_reads * 100), 1) if total_genre_reads > 0 else 0
                }
                for g in sorted_genres
            ]
        }

        character_stats = self.db.query(
            Character.name,
            func.count(func.distinct(ReadingProgress.comic_id)).label('appearances')
        ).join(comic_characters, Character.id == comic_characters.c.character_id) \
            .join(Comic, comic_characters.c.comic_id == Comic.id) \
            .join(ReadingProgress, Comic.id == ReadingProgress.comic_id) \
            .join(Volume, Comic.volume_id == Volume.id) \
            .join(Series, Volume.series_id == Series.id) \
            .filter(
            ReadingProgress.user_id == self.user.id,
            ReadingProgress.completed == True
        )

        if self.series_age_filter is not None:
            character_stats = character_stats.filter(self.series_age_filter)

        # SQL sorts and limits (critical - could be 100-1000+ characters)
        top_characters = [
            {'name': c.name, 'appearances': c.appearances}
            for c in character_stats.group_by(Character.id, Character.name)
            .order_by(func.count(func.distinct(ReadingProgress.comic_id)).desc())
            .limit(5).all()
        ]

        # === COLLECTION STATS (Single Query) ===
        total_comics_query = self.db.query(func.count(Comic.id)) \
            .join(Volume, Comic.volume_id == Volume.id) \
            .join(Series, Volume.series_id == Series.id)

        if self.series_age_filter is not None:
            total_comics_query = total_comics_query.filter(self.series_age_filter)

        total_available = total_comics_query.scalar() or 0

        collection_stats = {
            'total_available': total_available,
            'read_percentage': round((issues_read / total_available * 100), 1) if total_available > 0 else 0
        }

        # Series completed count (single query)
        # A series is "completed" if user has read all its comics
        series_completion_subquery = self.db.query(
            Series.id,
            func.count(Comic.id).label('total_comics'),
            func.count(case((ReadingProgress.completed == True, 1))).label('completed_comics')
        ).select_from(Series) \
            .join(Volume, Volume.series_id == Series.id) \
            .join(Comic, Comic.volume_id == Volume.id) \
            .outerjoin(
            ReadingProgress,
            and_(
                ReadingProgress.comic_id == Comic.id,
                ReadingProgress.user_id == self.user.id
            )
        )

        if self.series_age_filter is not None:
            series_completion_subquery = series_completion_subquery.filter(self.series_age_filter)

        series_completion_subquery = series_completion_subquery.group_by(Series.id).subquery()

        series_completed = self.db.query(func.count()).select_from(series_completion_subquery).filter(
            series_completion_subquery.c.total_comics == series_completion_subquery.c.completed_comics,
            series_completion_subquery.c.completed_comics > 0
        ).scalar() or 0

        # === HEATMAP DATA (Intensity Based) ===
        one_year_ago = datetime.now(timezone.utc) - timedelta(days=365)

        # We join through to Series to apply the "Poison Pill" age filters
        heatmap_query = self.db.query(
            func.date(ActivityLog.created_at).label('read_date'),
            func.sum(ActivityLog.pages_read).label('intensity')
        ).join(Comic, ActivityLog.comic_id == Comic.id) \
            .join(Volume, Comic.volume_id == Volume.id) \
            .join(Series, Volume.series_id == Series.id) \
            .filter(
            ActivityLog.user_id == self.user.id,
            ActivityLog.created_at >= one_year_ago
        )

        # Apply Row-Level Security (RLS) filters if they exist
        if self.series_age_filter is not None:
            heatmap_query = heatmap_query.filter(self.series_age_filter)

        heatmap_results = heatmap_query.group_by(func.date(ActivityLog.created_at)).all()
        heatmap_data = {row.read_date: row.intensity for row in heatmap_results}

        return {
            "stats": {
                "issues_read": issues_read,
                "pages_turned": total_pages,
                "time_read": time_read_str,
                "completed_comics": issues_read,
                "series_explored": series_explored,
                "series_completed": series_completed
            },
            "creators": {
                "top_writers": top_writers,
                "top_artists": top_artists
            },
            "publishers": {
                "top_publishers": top_publishers
            },
            "characters": {
                "top_characters": top_characters
            },
            "genres": genre_diversity,
            "reading_behavior": {
                'last_30_days': {
                    "comics_read": stats.recent_comics or 0,
                    "pages_read": stats.recent_pages or 0
                },
                "avg_days_to_complete": round(avg_days, 1),
                "reading_pace": reading_pace,
                "monthly_reading_goal": self.user.monthly_reading_goal,
            },
            "collection": collection_stats,
            "heatmap": heatmap_data,
            "active_streak": self.get_active_streak(),
        }

    def get_year_wrapped(self, year: int):


        # Date range for the year
        year_start = f"{year}-01-01"
        year_end = f"{year}-12-31 23:59:59"

        series_age_filter = get_series_age_restriction(self.user)

        # === BASIC YEAR STATS ===
        year_stats = self.db.query(
            func.count(func.distinct(ReadingProgress.comic_id)).label('comics_completed'),
            func.sum(Comic.page_count).label('total_pages'),
            func.count(func.distinct(Series.id)).label('series_explored'),
            func.count(func.distinct(Volume.id)).label('volumes_completed')
        ).join(Comic, ReadingProgress.comic_id == Comic.id) \
            .join(Volume).join(Series) \
            .filter(
            ReadingProgress.user_id == self.user.id,
            ReadingProgress.completed == True,
            ReadingProgress.last_read_at >= year_start,
            ReadingProgress.last_read_at <= year_end
        )

        if series_age_filter is not None:
            year_stats = year_stats.filter(series_age_filter)

        stats = year_stats.first()

        top_writer = self.db.query(
            Person.name,
            func.count(func.distinct(ReadingProgress.comic_id)).label('comics_read')
        ).join(ComicCredit, Person.id == ComicCredit.person_id) \
            .join(Comic, ComicCredit.comic_id == Comic.id) \
            .join(ReadingProgress, Comic.id == ReadingProgress.comic_id) \
            .join(Volume).join(Series) \
            .filter(
            ComicCredit.role == 'writer',
            ReadingProgress.user_id == self.user.id,
            ReadingProgress.completed == True,
            ReadingProgress.last_read_at >= year_start,
            ReadingProgress.last_read_at <= year_end
        )

        if series_age_filter is not None:
            top_writer = top_writer.filter(series_age_filter)

        top_writer = top_writer.group_by(Person.id, Person.name) \
            .order_by(func.count(func.distinct(ReadingProgress.comic_id)).desc()) \
            .first()

        top_artist = self.db.query(
            Person.name,
            func.count(func.distinct(ReadingProgress.comic_id)).label('comics_read')
        ).join(ComicCredit, Person.id == ComicCredit.person_id) \
            .join(Comic, ComicCredit.comic_id == Comic.id) \
            .join(ReadingProgress, Comic.id == ReadingProgress.comic_id) \
            .join(Volume).join(Series) \
            .filter(
            ComicCredit.role == 'penciller',
            ReadingProgress.user_id == self.user.id,
            ReadingProgress.completed == True,
            ReadingProgress.last_read_at >= year_start,
            ReadingProgress.last_read_at <= year_end
        )

        if series_age_filter is not None:
            top_artist = top_artist.filter(series_age_filter)

        top_artist = top_artist.group_by(Person.id, Person.name) \
            .order_by(func.count(func.distinct(ReadingProgress.comic_id)).desc()) \
            .first()

        # === TOP SERIES ===
        top_series = self.db.query(
            Series.name,
            func.count(func.distinct(ReadingProgress.comic_id)).label('issues_read')
        ).join(Volume).join(Comic).join(ReadingProgress) \
            .filter(
            ReadingProgress.user_id == self.user.id,
            ReadingProgress.completed == True,
            ReadingProgress.last_read_at >= year_start,
            ReadingProgress.last_read_at <= year_end
        )

        if series_age_filter is not None:
            top_series = top_series.filter(series_age_filter)

        top_series = top_series.group_by(Series.id, Series.name) \
            .order_by(func.count(func.distinct(ReadingProgress.comic_id)).desc()) \
            .first()

        top_genre = self.db.query(
            Genre.name,
            func.count(func.distinct(ReadingProgress.comic_id)).label('count')
        ).join(comic_genres, Genre.id == comic_genres.c.genre_id) \
            .join(Comic, comic_genres.c.comic_id == Comic.id) \
            .join(ReadingProgress, Comic.id == ReadingProgress.comic_id) \
            .join(Volume).join(Series) \
            .filter(
            ReadingProgress.user_id == self.user.id,
            ReadingProgress.completed == True,
            ReadingProgress.last_read_at >= year_start,
            ReadingProgress.last_read_at <= year_end
        )

        if series_age_filter is not None:
            top_genre = top_genre.filter(series_age_filter)

        top_genre = top_genre.group_by(Genre.id, Genre.name) \
            .order_by(func.count(func.distinct(ReadingProgress.comic_id)).desc()) \
            .first()

        top_character = self.db.query(
            Character.name,
            func.count(func.distinct(ReadingProgress.comic_id)).label('appearances')
        ).join(comic_characters, Character.id == comic_characters.c.character_id) \
            .join(Comic, comic_characters.c.comic_id == Comic.id) \
            .join(ReadingProgress, Comic.id == ReadingProgress.comic_id) \
            .join(Volume).join(Series) \
            .filter(
            ReadingProgress.user_id == self.user.id,
            ReadingProgress.completed == True,
            ReadingProgress.last_read_at >= year_start,
            ReadingProgress.last_read_at <= year_end
        )

        if series_age_filter is not None:
            top_character = top_character.filter(series_age_filter)

        top_character = top_character.group_by(Character.id, Character.name) \
            .order_by(func.count(func.distinct(ReadingProgress.comic_id)).desc()) \
            .first()

        # === BUSIEST MONTH ===
        busiest_month = self.db.query(
            func.strftime('%m', ReadingProgress.last_read_at).label('month'),
            func.count(func.distinct(ReadingProgress.comic_id)).label('count')
        ).join(Comic, ReadingProgress.comic_id == Comic.id) \
            .join(Volume).join(Series) \
            .filter(
            ReadingProgress.user_id == self.user.id,
            ReadingProgress.completed == True,
            ReadingProgress.last_read_at >= year_start,
            ReadingProgress.last_read_at <= year_end
        )

        if series_age_filter is not None:
            busiest_month = busiest_month.filter(series_age_filter)

        busiest_month = busiest_month.group_by('month') \
            .order_by(func.count(func.distinct(ReadingProgress.comic_id)).desc()) \
            .first()

        # Map month number to name
        month_names = ['January', 'February', 'March', 'April', 'May', 'June',
                       'July', 'August', 'September', 'October', 'November', 'December']
        busiest_month_name = month_names[int(busiest_month.month) - 1] if busiest_month else None

        # === LONGEST SERIES COMPLETED ===
        # Find the series with the most issues read in this year
        longest_series = self.db.query(
            Series.id,
            Series.name,
            func.count(func.distinct(Comic.id)).label('issues_completed')
        ).select_from(Series) \
            .join(Volume, Volume.series_id == Series.id) \
            .join(Comic, Comic.volume_id == Volume.id) \
            .join(ReadingProgress, ReadingProgress.comic_id == Comic.id) \
            .filter(
            ReadingProgress.user_id == self.user.id,
            ReadingProgress.completed == True,
            ReadingProgress.last_read_at >= year_start,
            ReadingProgress.last_read_at <= year_end
        )

        if series_age_filter is not None:
            longest_series = longest_series.filter(series_age_filter)

        longest_series = longest_series.group_by(Series.id, Series.name) \
            .order_by(func.count(func.distinct(Comic.id)).desc()) \
            .first()

        # === READING STREAK ===
        # Find longest consecutive days of reading
        reading_dates = self.db.query(
            func.date(ReadingProgress.last_read_at).label('read_date')
        ).join(Comic, ReadingProgress.comic_id == Comic.id) \
            .join(Volume).join(Series) \
            .filter(
            ReadingProgress.user_id == self.user.id,
            ReadingProgress.last_read_at >= year_start,
            ReadingProgress.last_read_at <= year_end
        )

        if series_age_filter is not None:
            reading_dates = reading_dates.filter(series_age_filter)

        reading_dates = reading_dates.distinct().order_by('read_date').all()

        # Calculate longest streak
        longest_streak = 0
        current_streak = 0
        prev_date = None

        for row in reading_dates:
            read_date = datetime.strptime(row.read_date, '%Y-%m-%d').date()
            if prev_date is None or (read_date - prev_date).days == 1:
                current_streak += 1
                longest_streak = max(longest_streak, current_streak)
            else:
                current_streak = 1
            prev_date = read_date

        # === CALCULATE FUN COMPARISONS ===
        total_pages = stats.total_pages or 0

        # Average comic is ~22 pages, graphic novel is ~150 pages
        graphic_novel_equivalent = round(total_pages / 150, 1)

        # Reading time (1.25 mins per page = 0.021 hours)
        reading_hours = round(total_pages * 0.021, 1)

        # Days worth of reading (assuming 8 hours per day)
        days_equivalent = round(reading_hours / 8, 1)

        # === READING VELOCITY & CONSISTENCY ===
        velocity_stats = self.db.query(
            func.count(func.distinct(func.date(ActivityLog.created_at))).label('active_days'),
            func.sum(ActivityLog.pages_read).label('total_pages'),
            func.avg(ActivityLog.pages_read).label('avg_pages_per_session')
        ).filter(
            ActivityLog.user_id == self.user.id,
            ActivityLog.created_at >= year_start,
            ActivityLog.created_at <= year_end
        ).first()

        active_days = velocity_stats.active_days or 1  # Avoid division by zero
        total_pages_year = velocity_stats.total_pages or 0
        pages_per_active_day = round(total_pages_year / active_days, 1)

        # Average "Burst" size (how many pages they read before the reader syncs)
        avg_burst = round(velocity_stats.avg_pages_per_session or 0, 1)

        return {
            "year": year,
            "stats": {
                "comics_completed": stats.comics_completed or 0,
                "total_pages": total_pages,
                "series_explored": stats.series_explored or 0,
                "volumes_completed": stats.volumes_completed or 0,
                "reading_hours": reading_hours,
                "graphic_novels_equivalent": graphic_novel_equivalent,
                "days_equivalent": days_equivalent
            },
            "favorites": {
                "top_writer": {
                    "name": top_writer.name if top_writer else None,
                    "comics_read": top_writer.comics_read if top_writer else 0
                },
                "top_artist": {
                    "name": top_artist.name if top_artist else None,
                    "comics_read": top_artist.comics_read if top_artist else 0
                },
                "top_series": {
                    "name": top_series.name if top_series else None,
                    "issues_read": top_series.issues_read if top_series else 0
                },
                "top_genre": {
                    "name": top_genre.name if top_genre else None,
                    "count": top_genre.count if top_genre else 0
                },
                "top_character": {
                    "name": top_character.name if top_character else None,
                    "appearances": top_character.appearances if top_character else 0
                }
            },
            "highlights": {
                "busiest_month": {
                    "name": busiest_month_name,
                    "comics_read": busiest_month.count if busiest_month else 0
                },
                "longest_streak": longest_streak,
                "longest_series_completed": {
                    "name": longest_series.name if longest_series else None,
                    "issues_completed": longest_series.issues_completed if longest_series else 0
                }
            },
            "fun_facts": {
                "if_this_was_novels": f"That's like reading {graphic_novel_equivalent} graphic novels!",
                "time_spent": f"You spent {reading_hours} hours reading comics this year",
                "marathon": f"That's {days_equivalent} full days of reading!" if days_equivalent >= 1 else f"That's {reading_hours} hours of reading!"
            },
            "velocity": {
                "active_days": active_days,
                "total_pages_year": total_pages_year,
                "pages_per_active_day": pages_per_active_day,
                "avg_burst": avg_burst
            }
        }

    def get_active_streak(self) -> int:
        """Calculates the current consecutive days of reading activity"""
        # 1. Get unique dates of activity (Ordered Descending)
        activity_dates = self.db.query(
            func.date(ActivityLog.created_at).label('read_date')
        ).filter(ActivityLog.user_id == self.user.id) \
            .group_by('read_date') \
            .order_by(func.date(ActivityLog.created_at).desc()).all()

        if not activity_dates:
            return 0

        today = datetime.now(timezone.utc).date()
        yesterday = today - timedelta(days=1)

        # Convert strings from SQLite to date objects
        dates = [datetime.strptime(d.read_date, '%Y-%m-%d').date() for d in activity_dates]

        # 2. If no activity today OR yesterday, the streak is broken
        if dates[0] < yesterday:
            return 0

        # 3. Count backwards
        streak = 0
        current_check = dates[0]

        for d in dates:
            if d == current_check:
                streak += 1
                current_check -= timedelta(days=1)
            else:
                break

        return streak

