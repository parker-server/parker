from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

import httpx
import portalocker

from app.config import settings


GITHUB_TAGS_API_URL = "https://api.github.com/repos/parker-server/parker/tags?per_page=100"
GITHUB_TAG_BASE_URL = "https://github.com/parker-server/parker/tree"
VERSION_CHECK_CACHE_SECONDS = 6 * 60 * 60
VERSION_CHECK_FAILURE_CACHE_SECONDS = 5 * 60
VERSION_CHECK_TIMEOUT = httpx.Timeout(connect=2.0, read=3.0, write=2.0, pool=2.0)
SEMVER_TAG_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
VERSION_CHECK_CACHE_FILE = settings.cache_dir / "version_check.json"
VERSION_CHECK_LOCK_FILE = settings.cache_dir / "version_check.lock"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VersionCheckStatus:
    current_version: str
    latest_tag: str | None
    latest_version: str | None
    update_available: bool
    status: str
    checked_at: str
    latest_url: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_version": self.current_version,
            "latest_tag": self.latest_tag,
            "latest_version": self.latest_version,
            "update_available": self.update_available,
            "status": self.status,
            "checked_at": self.checked_at,
            "latest_url": self.latest_url,
            "error": self.error,
        }


_cache_lock = threading.Lock()


def _checked_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_now() -> float:
    return time.time()


def parse_version(value: str | None) -> tuple[int, int, int] | None:
    if not value:
        return None

    match = SEMVER_TAG_PATTERN.match(value.strip())
    if not match:
        return None

    return tuple(int(part) for part in match.groups())


def _version_string(version: tuple[int, int, int] | None) -> str | None:
    if version is None:
        return None
    return ".".join(str(part) for part in version)


def _tag_name(tag: Mapping[str, Any] | str | Any) -> str | None:
    if isinstance(tag, str):
        return tag

    if not isinstance(tag, Mapping):
        return None

    name = tag.get("name")
    return name if isinstance(name, str) else None


def build_version_check_status(
    current_version: str,
    tags: Sequence[Mapping[str, Any] | str],
) -> VersionCheckStatus:
    current = parse_version(current_version)
    parsed_tags = []
    for tag in tags:
        tag_name = _tag_name(tag)
        parsed = parse_version(tag_name)
        if tag_name and parsed:
            parsed_tags.append((tag_name, parsed))

    if not parsed_tags:
        return VersionCheckStatus(
            current_version=current_version,
            latest_tag=None,
            latest_version=None,
            update_available=False,
            status="unavailable",
            checked_at=_checked_at(),
            error="No semantic version tags were found.",
        )

    latest_tag, latest_version = max(parsed_tags, key=lambda item: item[1])
    latest_url = f"{GITHUB_TAG_BASE_URL}/{latest_tag}"

    if current is None:
        return VersionCheckStatus(
            current_version=current_version,
            latest_tag=latest_tag,
            latest_version=_version_string(latest_version),
            update_available=False,
            status="unknown",
            checked_at=_checked_at(),
            latest_url=latest_url,
            error="Current version could not be compared.",
        )

    update_available = latest_version > current
    return VersionCheckStatus(
        current_version=current_version,
        latest_tag=latest_tag,
        latest_version=_version_string(latest_version),
        update_available=update_available,
        status="update_available" if update_available else "current",
        checked_at=_checked_at(),
        latest_url=latest_url,
    )


def _status_from_dict(payload: Any) -> VersionCheckStatus | None:
    if not isinstance(payload, Mapping):
        return None

    try:
        current_version = payload["current_version"]
        latest_tag = payload["latest_tag"]
        latest_version = payload["latest_version"]
        update_available = payload["update_available"]
        status = payload["status"]
        checked_at = payload["checked_at"]
        latest_url = payload["latest_url"]
        error = payload["error"]
    except KeyError:
        return None

    if (
        not isinstance(current_version, str)
        or not isinstance(update_available, bool)
        or not isinstance(status, str)
        or not isinstance(checked_at, str)
    ):
        return None

    optional_strings = (latest_tag, latest_version, latest_url, error)
    if any(value is not None and not isinstance(value, str) for value in optional_strings):
        return None

    return VersionCheckStatus(
        current_version=current_version,
        latest_tag=latest_tag,
        latest_version=latest_version,
        update_available=update_available,
        status=status,
        checked_at=checked_at,
        latest_url=latest_url,
        error=error,
    )


def _cache_ttl(status: VersionCheckStatus) -> int:
    if status.status == "unavailable":
        return VERSION_CHECK_FAILURE_CACHE_SECONDS
    return VERSION_CHECK_CACHE_SECONDS


def _read_cached_status(current_version: str, now: float) -> VersionCheckStatus | None:
    try:
        payload = json.loads(VERSION_CHECK_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(payload, Mapping):
        return None

    cached_at = payload.get("cached_at")
    if isinstance(cached_at, bool) or not isinstance(cached_at, (int, float)):
        return None

    status = _status_from_dict(payload.get("status"))
    if status is None or status.current_version != current_version:
        return None

    age = now - float(cached_at)
    if age < 0 or age >= _cache_ttl(status):
        return None

    return status


def _write_cached_status(status: VersionCheckStatus) -> None:
    VERSION_CHECK_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cached_at": _cache_now(),
        "status": status.to_dict(),
    }
    temp_path = VERSION_CHECK_CACHE_FILE.with_name(
        f".{VERSION_CHECK_CACHE_FILE.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )

    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, VERSION_CHECK_CACHE_FILE)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def _shared_cache_lock() -> Iterator[None]:
    VERSION_CHECK_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

    with _cache_lock:
        with VERSION_CHECK_LOCK_FILE.open("a+", encoding="utf-8") as lock_file:
            portalocker.lock(lock_file, portalocker.LOCK_EX)
            try:
                yield
            finally:
                portalocker.unlock(lock_file)


def clear_version_check_cache() -> None:
    try:
        with _shared_cache_lock():
            try:
                VERSION_CHECK_CACHE_FILE.unlink()
            except FileNotFoundError:
                pass
    except (OSError, portalocker.exceptions.LockException):
        try:
            VERSION_CHECK_CACHE_FILE.unlink()
        except OSError:
            pass


def _fetch_github_tags() -> Sequence[Mapping[str, Any]]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Parker-Version-Check",
    }
    with httpx.Client(timeout=VERSION_CHECK_TIMEOUT, headers=headers) as client:
        response = client.get(GITHUB_TAGS_API_URL)
        response.raise_for_status()
        data = response.json()

    if not isinstance(data, list):
        raise ValueError("GitHub tags response was not a list.")

    return data


def _refresh_version_check_status(current_version: str) -> VersionCheckStatus:
    try:
        return build_version_check_status(current_version, _fetch_github_tags())
    except (httpx.HTTPError, ValueError) as exc:
        return VersionCheckStatus(
            current_version=current_version,
            latest_tag=None,
            latest_version=None,
            update_available=False,
            status="unavailable",
            checked_at=_checked_at(),
            error=f"Unable to check GitHub tags: {exc}",
        )


def get_version_check_status(current_version: str, force_refresh: bool = False) -> VersionCheckStatus:
    if not force_refresh:
        cached_status = _read_cached_status(current_version, _cache_now())
        if cached_status is not None:
            return cached_status

    try:
        with _shared_cache_lock():
            if not force_refresh:
                cached_status = _read_cached_status(current_version, _cache_now())
                if cached_status is not None:
                    return cached_status

            status = _refresh_version_check_status(current_version)
            try:
                _write_cached_status(status)
            except OSError as exc:
                logger.warning("Unable to write version-check cache: %s", exc)
            return status
    except (OSError, portalocker.exceptions.LockException) as exc:
        logger.warning("Unable to use version-check cache lock: %s", exc)
        return _refresh_version_check_status(current_version)
