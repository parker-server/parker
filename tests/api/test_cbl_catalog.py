import pytest

import app.services.cbl_catalog_service as cbl_catalog_service_module
import app.services.cbl_source_service as cbl_source_service_module
from app.services.cbl_catalog_service import CBLCatalogService
from app.models.cbl_source import CBLSource
from app.models.reading_list import ReadingList
from app.models.comic import Volume
from app.models.series import Series
from tests.factories import create_comic, create_library_with_root

VALID_CBL = (
    b'<?xml version="1.0"?><ReadingList><Name>API Catalog List</Name>'
    b'<Books><Book Series="Catalog Series" Number="1" /></Books></ReadingList>'
)

LISTING_JSON = [
    {"name": "Marvel", "path": "Marvel", "type": "dir"},
    {"name": "readme.txt", "path": "readme.txt", "type": "file"},
    {"name": "Infinity-Gauntlet.cbl", "path": "Infinity-Gauntlet.cbl", "type": "file"},
]

FILE_META_JSON = {
    "name": "Infinity-Gauntlet.cbl",
    "path": "Infinity-Gauntlet.cbl",
    "type": "file",
    "download_url": "https://raw.githubusercontent.com/DieselTech/CBL-ReadingLists/main/Infinity-Gauntlet.cbl",
}


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
    def __init__(self, get_response=None, stream_chunks=None):
        self._get_response = get_response
        self._stream_chunks = stream_chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        return self._get_response

    def stream(self, method, url, headers=None):
        return _FakeStreamCtx(_FakeStreamResponse(self._stream_chunks or []))


def _patch_client_sequence(monkeypatch, clients):
    queue = list(clients)

    def factory(*args, **kwargs):
        return queue.pop(0)

    monkeypatch.setattr(cbl_catalog_service_module.httpx, "AsyncClient", factory)


def _patch_cbl_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(cbl_source_service_module.settings, "cbl_dir", tmp_path / "cbl")


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    CBLCatalogService._cache.clear()
    yield
    CBLCatalogService._cache.clear()


def test_browse_requires_admin(auth_client):
    response = auth_client.get("/api/cbl-catalog/browse")
    assert response.status_code == 400


def test_browse_success_filters_entries(admin_client, monkeypatch):
    _patch_client_sequence(monkeypatch, [_FakeCatalogClient(get_response=_FakeGetResponse(200, LISTING_JSON))])

    response = admin_client.get("/api/cbl-catalog/browse")

    assert response.status_code == 200
    names = [e["name"] for e in response.json()["entries"]]
    assert names == ["Marvel", "Infinity-Gauntlet.cbl"]


def test_browse_not_found_maps_to_404(admin_client, monkeypatch):
    _patch_client_sequence(monkeypatch, [_FakeCatalogClient(get_response=_FakeGetResponse(404, {"message": "Not Found"}))])

    response = admin_client.get("/api/cbl-catalog/browse?path=Nope")

    assert response.status_code == 404


def test_browse_rate_limited_maps_to_502(admin_client, monkeypatch):
    _patch_client_sequence(monkeypatch, [_FakeCatalogClient(get_response=_FakeGetResponse(403, {"message": "rate limited"}))])

    response = admin_client.get("/api/cbl-catalog/browse")

    assert response.status_code == 502


def test_preview_success(admin_client, monkeypatch):
    _patch_client_sequence(monkeypatch, [
        _FakeCatalogClient(get_response=_FakeGetResponse(200, FILE_META_JSON)),
        _FakeCatalogClient(stream_chunks=[VALID_CBL]),
    ])

    response = admin_client.get("/api/cbl-catalog/preview?path=Infinity-Gauntlet.cbl")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "API Catalog List"
    assert payload["entry_count"] == 1


def test_import_creates_source_and_reading_list(admin_client, db, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)

    library = create_library_with_root(db, "cbl-catalog-api-lib", str(tmp_path / "lib"))
    series = Series(name="Catalog Series", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()
    comic = create_comic(
        db, volume, library.active_root, "catalog-1.cbz",
        number="1", filename="catalog-1.cbz", page_count=1,
    )
    db.commit()

    _patch_client_sequence(monkeypatch, [
        _FakeCatalogClient(get_response=_FakeGetResponse(200, FILE_META_JSON)),
        _FakeCatalogClient(stream_chunks=[VALID_CBL]),
    ])

    response = admin_client.post("/api/cbl-catalog/import", json={"path": "Infinity-Gauntlet.cbl"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["origin"] == "catalog"
    assert payload["catalog_provider"] == "dieseltech"
    assert payload["catalog_path"] == "Infinity-Gauntlet.cbl"
    assert payload["entry_count"] == 1

    source = db.query(CBLSource).filter(CBLSource.id == payload["id"]).first()
    reading_list = db.query(ReadingList).filter(ReadingList.source_cbl_id == source.id).first()
    assert reading_list is not None
    assert reading_list.name == "API Catalog List"


def test_import_duplicate_maps_to_400(admin_client, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)

    _patch_client_sequence(monkeypatch, [
        _FakeCatalogClient(get_response=_FakeGetResponse(200, FILE_META_JSON)),
        _FakeCatalogClient(stream_chunks=[VALID_CBL]),
    ])
    first = admin_client.post("/api/cbl-catalog/import", json={"path": "Infinity-Gauntlet.cbl"})
    assert first.status_code == 200

    _patch_client_sequence(monkeypatch, [
        _FakeCatalogClient(get_response=_FakeGetResponse(200, FILE_META_JSON)),
        _FakeCatalogClient(stream_chunks=[VALID_CBL]),
    ])
    second = admin_client.post("/api/cbl-catalog/import", json={"path": "Infinity-Gauntlet.cbl"})

    assert second.status_code == 400
    assert "already imported" in second.json()["detail"]
