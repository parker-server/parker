# Import all models here so SQLAlchemy can set up relationships
from app.models.library import Library
from app.models.series import Series
from app.models.comic import Volume, Comic  # Both Volume and Comic are in comic.py
from app.models.tags import Character, Team, Location
from app.models.credits import Person, ComicCredit
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.collection import Collection, CollectionItem
from app.models.reading_progress import ReadingProgress
from app.models.job import ScanJob
from app.models.user import User
from app.models.interactions import UserSeries
from app.models.saved_search import SavedSearch
from app.models.setting import SystemSetting

# This ensures all models are loaded before relationships are configured
__all__ = [
    'Library', 'Series', 'Volume', 'Comic',
    'Character', 'Team', 'Location',
    'Person', 'ComicCredit',
    'ReadingList', 'ReadingListItem',
    'Collection', 'CollectionItem',
    'ReadingProgress',
    'ScanJob',
    'User',
    'UserSeries',
    'SavedSearch',
    'SystemSetting',

]


# Import other models here as we create them