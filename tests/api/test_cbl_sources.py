import hashlib
from pathlib import Path

import app.services.cbl_catalog_service as cbl_catalog_service_module
import app.services.cbl_source_service as cbl_source_service_module
from app.models.cbl_source import CBLSource

VALID_CBL = (
    b'<?xml version="1.0"?><ReadingList><Name>API Test List</Name>'
    b'<Books><Book Series="A" Number="1" /></Books></ReadingList>'
)


def _patch_cbl_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(cbl_source_service_module.settings, "cbl_dir", tmp_path / "cbl")


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


def _patch_catalog_client_sequence(monkeypatch, clients):
    queue = list(clients)

    def factory(*args, **kwargs):
        return queue.pop(0)

    monkeypatch.setattr(cbl_catalog_service_module.httpx, "AsyncClient", factory)


def test_upload_requires_admin(auth_client):
    response = auth_client.post(
        "/api/cbl-sources/upload",
        files={"file": ("test.cbl", VALID_CBL, "application/xml")},
    )
    assert response.status_code == 400  # get_current_active_superuser raises 400 for non-admins


def test_upload_success_creates_source(admin_client, db, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)

    response = admin_client.post(
        "/api/cbl-sources/upload",
        files={"file": ("api-test.cbl", VALID_CBL, "application/xml")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["display_name"] == "api-test"
    assert payload["origin"] == "upload"
    assert payload["entry_count"] == 1
    assert payload["reading_list_id"] is not None
    assert payload["reading_list_name"] == "API Test List"


def test_list_includes_reading_list_fields(admin_client, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)

    admin_client.post(
        "/api/cbl-sources/upload",
        files={"file": ("list-fields.cbl", VALID_CBL, "application/xml")},
    )

    response = admin_client.get("/api/cbl-sources/")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["reading_list_id"] is not None
    assert item["reading_list_name"] == "API Test List"


def test_upload_rejects_bad_extension(admin_client, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)

    response = admin_client.post(
        "/api/cbl-sources/upload",
        files={"file": ("not-a-list.txt", VALID_CBL, "text/plain")},
    )

    assert response.status_code == 400
    assert "extension" in response.json()["detail"]


def test_upload_rejects_duplicate(admin_client, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)

    first = admin_client.post(
        "/api/cbl-sources/upload",
        files={"file": ("first.cbl", VALID_CBL, "application/xml")},
    )
    assert first.status_code == 200

    second = admin_client.post(
        "/api/cbl-sources/upload",
        files={"file": ("second.cbl", VALID_CBL, "application/xml")},
    )
    assert second.status_code == 400
    assert "already imported" in second.json()["detail"]


def test_list_and_detail(admin_client, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)

    uploaded = admin_client.post(
        "/api/cbl-sources/upload",
        files={"file": ("list-detail.cbl", VALID_CBL, "application/xml")},
    ).json()

    listed = admin_client.get("/api/cbl-sources/")
    assert listed.status_code == 200
    assert any(item["id"] == uploaded["id"] for item in listed.json()["items"])

    detail = admin_client.get(f"/api/cbl-sources/{uploaded['id']}")
    assert detail.status_code == 200
    assert detail.json()["id"] == uploaded["id"]

    missing = admin_client.get("/api/cbl-sources/999999")
    assert missing.status_code == 404


def test_delete_removes_source(admin_client, db, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)

    uploaded = admin_client.post(
        "/api/cbl-sources/upload",
        files={"file": ("delete-me.cbl", VALID_CBL, "application/xml")},
    ).json()

    deleted = admin_client.delete(f"/api/cbl-sources/{uploaded['id']}")
    assert deleted.status_code == 200

    missing = admin_client.delete(f"/api/cbl-sources/{uploaded['id']}")
    assert missing.status_code == 404

    assert db.query(CBLSource).filter(CBLSource.id == uploaded["id"]).count() == 0


def test_import_url_rejects_non_https(admin_client):
    response = admin_client.post("/api/cbl-sources/url", json={"url": "http://example.com/list.cbl"})
    assert response.status_code == 400
    assert "https" in response.json()["detail"]


def test_refresh_rejects_source_with_no_url(admin_client, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)

    uploaded = admin_client.post(
        "/api/cbl-sources/upload",
        files={"file": ("no-url.cbl", VALID_CBL, "application/xml")},
    ).json()

    response = admin_client.post(f"/api/cbl-sources/{uploaded['id']}/refresh")
    assert response.status_code == 400
    assert "no URL to refresh" in response.json()["detail"]


def test_refresh_missing_source_404(admin_client):
    response = admin_client.post("/api/cbl-sources/999999/refresh")
    assert response.status_code == 404


def test_refresh_dispatches_to_catalog_path_for_catalog_origin_sources(admin_client, db, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)

    fingerprint = hashlib.sha256(VALID_CBL).hexdigest()
    cbl_dir = tmp_path / "cbl"
    cbl_dir.mkdir(parents=True, exist_ok=True)
    stored_path = cbl_dir / f"{fingerprint}.cbl"
    stored_path.write_bytes(VALID_CBL)

    source = CBLSource(
        display_name="Catalog Source",
        stored_path=str(stored_path),
        original_filename="Catalog-Source.cbl",
        origin="catalog",
        catalog_provider="dieseltech",
        catalog_path="Marvel/Catalog-Source.cbl",
        fingerprint=fingerprint,
        last_refresh_status="never",
    )
    db.add(source)
    db.commit()

    new_content = VALID_CBL.replace(b"API Test List", b"Updated Catalog Source")
    new_meta = {
        "name": "Catalog-Source.cbl",
        "path": "Marvel/Catalog-Source.cbl",
        "type": "file",
        "download_url": "https://raw.githubusercontent.com/DieselTech/CBL-ReadingLists/main/Marvel/Catalog-Source.cbl",
    }
    _patch_catalog_client_sequence(monkeypatch, [
        _FakeCatalogClient(get_response=_FakeGetResponse(200, new_meta)),
        _FakeCatalogClient(stream_chunks=[new_content]),
    ])

    response = admin_client.post(f"/api/cbl-sources/{source.id}/refresh")

    assert response.status_code == 200
    payload = response.json()
    assert payload["last_refresh_status"] == "ok"

    db.refresh(source)
    assert Path(source.stored_path).read_bytes() == new_content
