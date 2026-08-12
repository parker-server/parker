from functools import lru_cache
import time
from threading import Lock

from app.config import settings
from app.database import SessionLocal

_CACHE_TOKEN_FILE = settings.cache_dir / "settings.cache.token"
_CACHE_POLL_INTERVAL_NS = 500_000_000  # 0.5s

_cache_lock = Lock()
_last_seen_token = None
_last_token_check_ns = 0


# 1. Basic Fetcher (Safe for background tasks)
def get_system_setting(key: str, default=None):
    """
    Opens a temporary DB connection, fetches one setting, and closes.
    Use this in background jobs or utility scripts.
    """

    # Import here to avoid circular dependency with settings_service.py
    from app.services.settings_service import SettingsService

    with SessionLocal() as db:
        svc = SettingsService(db)
        val = svc.get(key)
        return val if val is not None else default


def _read_cache_token() -> str:
    try:
        return _CACHE_TOKEN_FILE.read_text(encoding="ascii").strip()
    except OSError:
        return ""


def _sync_cache_if_token_changed() -> None:
    global _last_seen_token, _last_token_check_ns

    now = time.monotonic_ns()
    if now - _last_token_check_ns < _CACHE_POLL_INTERVAL_NS:
        return

    with _cache_lock:
        now = time.monotonic_ns()
        if now - _last_token_check_ns < _CACHE_POLL_INTERVAL_NS:
            return

        _last_token_check_ns = now
        token = _read_cache_token()

        if token != _last_seen_token:
            _get_cached_setting.cache_clear()
            _last_seen_token = token


# 2. Cached Fetcher (High Performance)
# Use this for settings accessed inside tight loops (like scanning)
# to avoid hitting the DB 100 times a second.
@lru_cache(maxsize=50)
def _get_cached_setting(key: str, default=None):
    return get_system_setting(key, default)


def get_cached_setting(key: str, default=None):
    _sync_cache_if_token_changed()
    return _get_cached_setting(key, default)


def invalidate_settings_cache():
    global _last_seen_token, _last_token_check_ns

    with _cache_lock:
        _get_cached_setting.cache_clear()
        _last_token_check_ns = time.monotonic_ns()

        try:
            settings.cache_dir.mkdir(parents=True, exist_ok=True)
            token = str(time.time_ns())
            _CACHE_TOKEN_FILE.write_text(token, encoding="ascii")
            _last_seen_token = token
        except OSError:
            # Best effort: local process is already invalidated.
            _last_seen_token = None
            _last_token_check_ns = 0

    return
