# Import all models here so SQLAlchemy can set up relationships
from app.models.library import Library
from app.models.series import Series
from app.models.comic import Volume, Comic  # Both Volume and Comic are in comic.py
from app.models.tags import Character, Team, Location, Genre
from app.models.credits import Person, ComicCredit
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.collection import Collection, CollectionItem
from app.models.reading_progress import ReadingProgress
from app.models.job import ScanJob
from app.models.user import User
from app.models.interactions import UserSeries
from app.models.saved_search import SavedSearch
from app.models.setting import SystemSetting
from app.models.pull_list import PullList, PullListItem
from app.models.smart_list import SmartList
from app.models.activity_log import ActivityLog

# This ensures all models are loaded before relationships are configured
__all__ = [
    'Library', 'Series', 'Volume', 'Comic',
    'Character', 'Team', 'Location', 'Genre',
    'Person', 'ComicCredit',
    'ReadingList', 'ReadingListItem',
    'Collection', 'CollectionItem',
    'ReadingProgress', 'ActivityLog',
    'ScanJob',
    'User',
    'UserSeries',
    'SavedSearch', 'SmartList',
    'SystemSetting',
    'PullList', 'PullListItem',

]


# Import other models here as we create them