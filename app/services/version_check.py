from __future__ import annotations

import re
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx


GITHUB_TAGS_API_URL = "https://api.github.com/repos/parker-server/parker/tags?per_page=100"
GITHUB_TAG_BASE_URL = "https://github.com/parker-server/parker/tree"
VERSION_CHECK_CACHE_SECONDS = 6 * 60 * 60
VERSION_CHECK_TIMEOUT = httpx.Timeout(connect=2.0, read=3.0, write=2.0, pool=2.0)
SEMVER_TAG_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


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
_cached_status: tuple[float, str, VersionCheckStatus] | None = None


def _checked_at() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def clear_version_check_cache() -> None:
    global _cached_status

    with _cache_lock:
        _cached_status = None


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


def get_version_check_status(current_version: str, force_refresh: bool = False) -> VersionCheckStatus:
    global _cached_status

    now = time.monotonic()
    with _cache_lock:
        if (
            not force_refresh
            and _cached_status is not None
            and _cached_status[1] == current_version
            and now - _cached_status[0] < VERSION_CHECK_CACHE_SECONDS
        ):
            return _cached_status[2]

    try:
        status = build_version_check_status(current_version, _fetch_github_tags())
    except (httpx.HTTPError, ValueError) as exc:
        status = VersionCheckStatus(
            current_version=current_version,
            latest_tag=None,
            latest_version=None,
            update_available=False,
            status="unavailable",
            checked_at=_checked_at(),
            error=f"Unable to check GitHub tags: {exc}",
        )

    with _cache_lock:
        _cached_status = (time.monotonic(), current_version, status)

    return status
