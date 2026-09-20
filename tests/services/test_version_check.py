import json

import httpx

import app.services.version_check as version_check
from app.services.version_check import (
    VERSION_CHECK_CACHE_SECONDS,
    VERSION_CHECK_FAILURE_CACHE_SECONDS,
    build_version_check_status,
    clear_version_check_cache,
    get_version_check_status,
    parse_version,
)


def _use_temp_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(version_check, "VERSION_CHECK_CACHE_FILE", tmp_path / "version_check.json")
    monkeypatch.setattr(version_check, "VERSION_CHECK_LOCK_FILE", tmp_path / "version_check.lock")


def test_parse_version_accepts_plain_and_prefixed_semver():
    assert parse_version("0.1.34") == (0, 1, 34)
    assert parse_version("v0.1.35") == (0, 1, 35)
    assert parse_version("release-0.1.35") is None


def test_build_version_check_status_reports_newer_tag():
    status = build_version_check_status(
        "0.1.34",
        [
            {"name": "v0.1.33"},
            {"name": "not-a-version"},
            {"name": "v0.1.35"},
        ],
    )

    assert status.status == "update_available"
    assert status.update_available is True
    assert status.latest_tag == "v0.1.35"
    assert status.latest_version == "0.1.35"
    assert status.latest_url == "https://github.com/parker-server/parker/tree/v0.1.35"


def test_build_version_check_status_reports_current_when_latest_matches():
    status = build_version_check_status(
        "0.1.35",
        [
            {"name": "v0.1.34"},
            {"name": "v0.1.35"},
        ],
    )

    assert status.status == "current"
    assert status.update_available is False
    assert status.latest_tag == "v0.1.35"


def test_build_version_check_status_handles_missing_semver_tags():
    status = build_version_check_status("0.1.35", [{"name": "latest"}])

    assert status.status == "unavailable"
    assert status.update_available is False
    assert status.latest_tag is None
    assert status.error == "No semantic version tags were found."


def test_version_check_reuses_shared_file_cache(monkeypatch, tmp_path):
    _use_temp_cache(monkeypatch, tmp_path)
    fetch_calls = 0

    def fetch_tags():
        nonlocal fetch_calls
        fetch_calls += 1
        return [{"name": "v0.1.36"}]

    monkeypatch.setattr(version_check, "_fetch_github_tags", fetch_tags)

    first = get_version_check_status("0.1.35")
    second = get_version_check_status("0.1.35")

    assert first.status == "update_available"
    assert second == first
    assert fetch_calls == 1

    payload = json.loads(version_check.VERSION_CHECK_CACHE_FILE.read_text(encoding="utf-8"))
    assert payload["status"]["latest_tag"] == "v0.1.36"
    assert payload["status"]["current_version"] == "0.1.35"


def test_version_check_refreshes_expired_success_cache(monkeypatch, tmp_path):
    _use_temp_cache(monkeypatch, tmp_path)
    now = [1000.0]
    responses = iter(
        [
            [{"name": "v0.1.35"}],
            [{"name": "v0.1.36"}],
        ]
    )
    fetch_calls = 0

    def fetch_tags():
        nonlocal fetch_calls
        fetch_calls += 1
        return next(responses)

    monkeypatch.setattr(version_check, "_cache_now", lambda: now[0])
    monkeypatch.setattr(version_check, "_fetch_github_tags", fetch_tags)

    first = get_version_check_status("0.1.35")
    assert first.status == "current"

    now[0] += VERSION_CHECK_CACHE_SECONDS - 1
    second = get_version_check_status("0.1.35")
    assert second.status == "current"
    assert fetch_calls == 1

    now[0] += 2
    third = get_version_check_status("0.1.35")
    assert third.status == "update_available"
    assert third.latest_tag == "v0.1.36"
    assert fetch_calls == 2


def test_version_check_uses_shorter_ttl_for_failures(monkeypatch, tmp_path):
    _use_temp_cache(monkeypatch, tmp_path)
    now = [2000.0]
    fetch_calls = 0

    def fetch_tags():
        nonlocal fetch_calls
        fetch_calls += 1
        if fetch_calls == 1:
            raise httpx.HTTPError("network down")
        return [{"name": "v0.1.36"}]

    monkeypatch.setattr(version_check, "_cache_now", lambda: now[0])
    monkeypatch.setattr(version_check, "_fetch_github_tags", fetch_tags)

    first = get_version_check_status("0.1.35")
    assert first.status == "unavailable"
    assert "network down" in first.error

    now[0] += VERSION_CHECK_FAILURE_CACHE_SECONDS - 1
    second = get_version_check_status("0.1.35")
    assert second.status == "unavailable"
    assert fetch_calls == 1

    now[0] += 2
    third = get_version_check_status("0.1.35")
    assert third.status == "update_available"
    assert third.latest_tag == "v0.1.36"
    assert fetch_calls == 2


def test_version_check_rechecks_cache_after_acquiring_lock(monkeypatch, tmp_path):
    _use_temp_cache(monkeypatch, tmp_path)
    cached = build_version_check_status("0.1.35", [{"name": "v0.1.36"}])
    reads = iter([None, cached])

    monkeypatch.setattr(version_check, "_read_cached_status", lambda *_args: next(reads))

    def unexpected_refresh(_current_version):
        raise AssertionError("GitHub should not be queried after another worker refreshes the cache")

    monkeypatch.setattr(version_check, "_refresh_version_check_status", unexpected_refresh)

    result = get_version_check_status("0.1.35")

    assert result == cached


def test_clear_version_check_cache_removes_shared_file(monkeypatch, tmp_path):
    _use_temp_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(version_check, "_fetch_github_tags", lambda: [{"name": "v0.1.36"}])

    get_version_check_status("0.1.35")
    assert version_check.VERSION_CHECK_CACHE_FILE.exists()

    clear_version_check_cache()

    assert not version_check.VERSION_CHECK_CACHE_FILE.exists()
