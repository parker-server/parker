from pathlib import Path

import httpx
import pytest

import app.services.cbl_catalog_service as cbl_catalog_service_module
import app.services.cbl_source_service as cbl_source_service_module
from tests.async_helpers import run_async
from app.services.cbl_catalog_service import (
    CBLCatalogNotFoundError,
    CBLCatalogService,
    CBLCatalogUpstreamError,
    CBLCatalogError,
)
from app.services.cbl_source_service import CBLSourceError, CBLSourceService

VALID_CBL = (
    b'<?xml version="1.0"?><ReadingList><Name>Catalog Test List</Name>'
    b'<Books><Book Series="A" Number="1" /></Books></ReadingList>'
)


class _FakeGetResponse:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json_data = json_data

    def json(self):
        return self._json_data


class _FakeStreamResponse:
    def __init__(self, chunks):
        self._chunks = chunks

    def raise_for_status(self):
        pass

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class _FakeStreamCtx:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class _FakeCatalogClient:
    """One instance == one `async with httpx.AsyncClient(...) as client:` block."""

    def __init__(self, get_response=None, stream_chunks=None, raise_on_get=None, raise_on_stream=None):
        self._get_response = get_response
        self._stream_chunks = stream_chunks
        self._raise_on_get = raise_on_get
        self._raise_on_stream = raise_on_stream

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        if self._raise_on_get:
            raise self._raise_on_get
        return self._get_response

    def stream(self, method, url, headers=None):
        if self._raise_on_stream:
            raise self._raise_on_stream
        return _FakeStreamCtx(_FakeStreamResponse(self._stream_chunks or []))


def _patch_client_sequence(monkeypatch, clients):
    queue = list(clients)

    def factory(*args, **kwargs):
        return queue.pop(0)

    monkeypatch.setattr(cbl_catalog_service_module.httpx, "AsyncClient", factory)
    return queue


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    CBLCatalogService._cache.clear()
    yield
    CBLCatalogService._cache.clear()


LISTING_JSON = [
    {"name": "Marvel", "path": "Marvel", "type": "dir"},
    {"name": "readme.txt", "path": "readme.txt", "type": "file"},
    {"name": "Infinity-Gauntlet.cbl", "path": "Infinity-Gauntlet.cbl", "type": "file"},
]

FILE_META_JSON = {
    "name": "Infinity-Gauntlet.cbl",
    "path": "Marvel/Infinity-Gauntlet.cbl",
    "type": "file",
    "download_url": "https://raw.githubusercontent.com/DieselTech/CBL-ReadingLists/main/Marvel/Infinity-Gauntlet.cbl",
}


def test_browse_filters_to_dirs_and_cbl_files_only(db, monkeypatch):
    _patch_client_sequence(monkeypatch, [_FakeCatalogClient(get_response=_FakeGetResponse(200, LISTING_JSON))])
    service = CBLCatalogService(db)

    result = run_async(service.browse(""))

    names = [e["name"] for e in result["entries"]]
    assert names == ["Marvel", "Infinity-Gauntlet.cbl"]
    assert result["entries"][0]["type"] == "dir"
    assert result["entries"][1]["type"] == "file"


def test_browse_uses_cache_within_ttl(db, monkeypatch):
    queue = _patch_client_sequence(monkeypatch, [_FakeCatalogClient(get_response=_FakeGetResponse(200, LISTING_JSON))])
    service = CBLCatalogService(db)

    first = run_async(service.browse("Marvel"))
    second = run_async(service.browse("Marvel"))

    assert first == second
    assert queue == []  # only one AsyncClient() call happened -- second browse was served from cache


def test_browse_force_refresh_bypasses_cache(db, monkeypatch):
    other_listing = [{"name": "DC", "path": "DC", "type": "dir"}]
    _patch_client_sequence(monkeypatch, [
        _FakeCatalogClient(get_response=_FakeGetResponse(200, LISTING_JSON)),
        _FakeCatalogClient(get_response=_FakeGetResponse(200, other_listing)),
    ])
    service = CBLCatalogService(db)

    first = run_async(service.browse(""))
    second = run_async(service.browse("", force_refresh=True))

    assert first["entries"][0]["name"] == "Marvel"
    assert second["entries"][0]["name"] == "DC"


def test_browse_404_raises_not_found(db, monkeypatch):
    _patch_client_sequence(monkeypatch, [_FakeCatalogClient(get_response=_FakeGetResponse(404, {"message": "Not Found"}))])
    service = CBLCatalogService(db)

    with pytest.raises(CBLCatalogNotFoundError):
        run_async(service.browse("Nonexistent"))


def test_browse_rate_limit_raises_upstream_error(db, monkeypatch):
    _patch_client_sequence(monkeypatch, [_FakeCatalogClient(get_response=_FakeGetResponse(403, {"message": "rate limited"}))])
    service = CBLCatalogService(db)

    with pytest.raises(CBLCatalogUpstreamError):
        run_async(service.browse(""))


def test_browse_network_error_raises_upstream_error(db, monkeypatch):
    _patch_client_sequence(monkeypatch, [_FakeCatalogClient(raise_on_get=httpx.HTTPError("boom"))])
    service = CBLCatalogService(db)

    with pytest.raises(CBLCatalogUpstreamError):
        run_async(service.browse(""))


def test_browse_rejects_path_pointing_at_a_file(db, monkeypatch):
    _patch_client_sequence(monkeypatch, [_FakeCatalogClient(get_response=_FakeGetResponse(200, FILE_META_JSON))])
    service = CBLCatalogService(db)

    with pytest.raises(CBLCatalogNotFoundError):
        run_async(service.browse("Marvel/Infinity-Gauntlet.cbl"))


def test_preview_returns_name_entry_count_and_warnings(db, monkeypatch):
    _patch_client_sequence(monkeypatch, [
        _FakeCatalogClient(get_response=_FakeGetResponse(200, FILE_META_JSON)),
        _FakeCatalogClient(stream_chunks=[VALID_CBL]),
    ])
    service = CBLCatalogService(db)

    result = run_async(service.preview("Marvel/Infinity-Gauntlet.cbl"))

    assert result["name"] == "Catalog Test List"
    assert result["entry_count"] == 1
    assert result["warnings"] == []


def test_import_file_sets_catalog_origin_and_metadata(db, monkeypatch, tmp_path):
    monkeypatch.setattr(cbl_source_service_module.settings, "cbl_dir", tmp_path / "cbl")

    _patch_client_sequence(monkeypatch, [
        _FakeCatalogClient(get_response=_FakeGetResponse(200, FILE_META_JSON)),
        _FakeCatalogClient(stream_chunks=[VALID_CBL]),
    ])
    service = CBLCatalogService(db)

    source = run_async(service.import_file("Marvel/Infinity-Gauntlet.cbl"))
    db.commit()

    assert source.origin == "catalog"
    assert source.catalog_provider == "dieseltech"
    assert source.catalog_path == "Marvel/Infinity-Gauntlet.cbl"


def test_fetch_raw_bytes_rejects_non_github_host(db):
    service = CBLCatalogService(db)

    with pytest.raises(CBLCatalogError, match="unexpected host"):
        run_async(service._fetch_raw_bytes("https://evil.example.com/payload.cbl"))


def test_refresh_source_success_updates_fingerprint_and_removes_old_file(db, tmp_path, monkeypatch):
    monkeypatch.setattr(cbl_source_service_module.settings, "cbl_dir", tmp_path / "cbl")

    _patch_client_sequence(monkeypatch, [
        _FakeCatalogClient(get_response=_FakeGetResponse(200, FILE_META_JSON)),
        _FakeCatalogClient(stream_chunks=[VALID_CBL]),
    ])
    service = CBLCatalogService(db)
    source = run_async(service.import_file("Marvel/Infinity-Gauntlet.cbl"))
    db.commit()

    old_path = Path(source.stored_path)
    old_fingerprint = source.fingerprint
    assert old_path.exists()

    new_content = VALID_CBL.replace(b"Catalog Test List", b"Updated Catalog List")
    _patch_client_sequence(monkeypatch, [
        _FakeCatalogClient(get_response=_FakeGetResponse(200, FILE_META_JSON)),
        _FakeCatalogClient(stream_chunks=[new_content]),
    ])

    refreshed = run_async(service.refresh_source(source.id))
    db.commit()

    assert refreshed.last_refresh_status == "ok"
    assert refreshed.fingerprint != old_fingerprint
    assert not old_path.exists()
    assert Path(refreshed.stored_path).read_bytes() == new_content


def test_refresh_source_failure_keeps_last_good_state(db, tmp_path, monkeypatch):
    monkeypatch.setattr(cbl_source_service_module.settings, "cbl_dir", tmp_path / "cbl")

    _patch_client_sequence(monkeypatch, [
        _FakeCatalogClient(get_response=_FakeGetResponse(200, FILE_META_JSON)),
        _FakeCatalogClient(stream_chunks=[VALID_CBL]),
    ])
    service = CBLCatalogService(db)
    source = run_async(service.import_file("Marvel/Infinity-Gauntlet.cbl"))
    db.commit()

    original_fingerprint = source.fingerprint
    original_path = Path(source.stored_path)

    _patch_client_sequence(monkeypatch, [
        _FakeCatalogClient(get_response=_FakeGetResponse(404, {"message": "Not Found"})),
    ])

    refreshed = run_async(service.refresh_source(source.id))
    db.commit()

    assert refreshed.last_refresh_status == "failed"
    assert refreshed.fingerprint == original_fingerprint
    assert original_path.exists()
    assert original_path.read_bytes() == VALID_CBL


def test_refresh_source_rejects_unparseable_content_and_preserves_last_good_file(db, tmp_path, monkeypatch):
    monkeypatch.setattr(cbl_source_service_module.settings, "cbl_dir", tmp_path / "cbl")

    _patch_client_sequence(monkeypatch, [
        _FakeCatalogClient(get_response=_FakeGetResponse(200, FILE_META_JSON)),
        _FakeCatalogClient(stream_chunks=[VALID_CBL]),
    ])
    service = CBLCatalogService(db)
    source = run_async(service.import_file("Marvel/Infinity-Gauntlet.cbl"))
    db.commit()

    original_fingerprint = source.fingerprint
    original_path = Path(source.stored_path)

    # Passes the cheap "looks like CBL/XML" sniff but is not well-formed XML.
    broken_content = b'<?xml version="1.0"?><ReadingList><Name>Broken'
    _patch_client_sequence(monkeypatch, [
        _FakeCatalogClient(get_response=_FakeGetResponse(200, FILE_META_JSON)),
        _FakeCatalogClient(stream_chunks=[broken_content]),
    ])

    refreshed = run_async(service.refresh_source(source.id))
    db.commit()

    assert refreshed.last_refresh_status == "failed"
    assert refreshed.fingerprint == original_fingerprint
    assert original_path.exists()
    assert original_path.read_bytes() == VALID_CBL


def test_refresh_source_rejects_non_catalog_origin(db, tmp_path, monkeypatch):
    monkeypatch.setattr(cbl_source_service_module.settings, "cbl_dir", tmp_path / "cbl")

    source = CBLSourceService(db).import_upload(VALID_CBL, "uploaded.cbl")
    db.commit()

    service = CBLCatalogService(db)
    with pytest.raises(CBLSourceError, match="catalog path"):
        run_async(service.refresh_source(source.id))


def test_refresh_source_missing_id_raises_value_error(db):
    service = CBLCatalogService(db)

    with pytest.raises(ValueError):
        run_async(service.refresh_source(999999))
