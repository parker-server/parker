from functools import lru_cache
#from app.database import SessionLocal
#from app.services.settings_service import SettingsService

# 1. Basic Fetcher (Safe for background tasks)
def get_system_setting(key: str, default=None):
    """
    Opens a temporary DB connection, fetches one setting, and closes.
    Use this in background jobs or utility scripts.
    """
    #with SessionLocal() as db:
    #    svc = SettingsService(db)
    #    val = svc.get(key)
    #    return val if val is not None else default

# 2. Cached Fetcher (High Performance)
# Use this for settings accessed inside tight loops (like scanning)
# to avoid hitting the DB 100 times a second.
@lru_cache(maxsize=50)
def get_cached_setting(key: str, default=None):
    return get_system_setting(key, default)

def invalidate_settings_cache():
    #get_cached_setting.cache_clear()
    return