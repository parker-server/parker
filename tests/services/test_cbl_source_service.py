from pathlib import Path

import httpx
import pytest

import app.services.cbl_source_service as cbl_source_service_module
from app.services.cbl_source_service import CBLSourceError, CBLSourceService, _pin_url_to_address
from app.models.cbl_source import CBLSource
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.comic import Volume
from app.models.series import Series
from tests.async_helpers import run_async
from tests.factories import create_comic, create_library_with_root

VALID_CBL = (
    b'<?xml version="1.0"?><ReadingList><Name>Test List</Name>'
    b'<Books><Book Series="A" Number="1" /></Books></ReadingList>'
)


class _FakeResponse:
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


class _FakeAsyncClient:
    def __init__(self, chunks=None, raise_on_stream=None):
        self._chunks = chunks or []
        self._raise_on_stream = raise_on_stream
        self.stream_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, headers=None, extensions=None):
        self.stream_calls.append({"method": method, "url": url, "headers": headers, "extensions": extensions})
        if self._raise_on_stream:
            raise self._raise_on_stream
        return _FakeStreamCtx(_FakeResponse(self._chunks))


def _patch_fake_client(monkeypatch, chunks=None, raise_on_stream=None):
    created = []

    def factory(*a, **kw):
        client = _FakeAsyncClient(chunks=chunks, raise_on_stream=raise_on_stream)
        created.append(client)
        return client

    monkeypatch.setattr(cbl_source_service_module.httpx, "AsyncClient", factory)
    return created


def _patch_cbl_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(cbl_source_service_module.settings, "cbl_dir", tmp_path / "cbl")


FAKE_SAFE_ADDRESS = "203.0.113.5"


@pytest.fixture(autouse=True)
def _skip_host_check(monkeypatch):
    """Most tests exercise validation/storage logic, not real DNS -- bypass the
    host-safety/resolution check by default; specific tests re-enable it explicitly."""
    monkeypatch.setattr(
        CBLSourceService, "_resolve_safe_addresses", lambda self, host: [FAKE_SAFE_ADDRESS]
    )


def test_import_upload_success_writes_file_and_creates_source(db, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)
    service = CBLSourceService(db)

    source = service.import_upload(VALID_CBL, "my-event.cbl")
    db.commit()

    assert source.id is not None
    assert source.origin == "upload"
    assert source.display_name == "my-event"
    assert Path(source.stored_path).exists()
    assert Path(source.stored_path).read_bytes() == VALID_CBL


def test_import_upload_sets_catalog_fields_when_provided(db, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)
    service = CBLSourceService(db)

    source = service.import_upload(
        VALID_CBL, "catalog-file.cbl",
        origin="catalog", catalog_provider="dieseltech", catalog_path="Marvel/catalog-file.cbl",
    )
    db.commit()

    assert source.origin == "catalog"
    assert source.catalog_provider == "dieseltech"
    assert source.catalog_path == "Marvel/catalog-file.cbl"


def test_import_upload_rejects_duplicate_fingerprint(db, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)
    service = CBLSourceService(db)
    service.import_upload(VALID_CBL, "first.cbl")
    db.commit()

    with pytest.raises(CBLSourceError, match="already imported"):
        service.import_upload(VALID_CBL, "second.cbl")


def test_import_upload_rejects_bad_extension(db, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)
    service = CBLSourceService(db)

    with pytest.raises(CBLSourceError, match="extension"):
        service.import_upload(VALID_CBL, "not-a-cbl.txt")


def test_import_upload_rejects_oversized_file(db, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(cbl_source_service_module, "MAX_CBL_SIZE_BYTES", 10)
    service = CBLSourceService(db)

    with pytest.raises(CBLSourceError, match="too large"):
        service.import_upload(VALID_CBL, "big.cbl")


def test_import_upload_rejects_non_xml_content(db, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)
    service = CBLSourceService(db)

    with pytest.raises(CBLSourceError, match="does not look like"):
        service.import_upload(b"just some text, not xml", "fake.cbl")


def test_import_url_rejects_non_https(db):
    service = CBLSourceService(db)
    with pytest.raises(CBLSourceError, match="https"):
        run_async(service.import_url("http://example.com/list.cbl"))


def test_import_url_rejects_loopback_host(db, monkeypatch):
    monkeypatch.undo()  # restore the real _resolve_safe_addresses for this test only
    service = CBLSourceService(db)
    with pytest.raises(CBLSourceError, match="non-public"):
        run_async(service.import_url("https://127.0.0.1/list.cbl"))


def test_import_url_success_downloads_and_stores(db, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)
    _patch_fake_client(monkeypatch, chunks=[VALID_CBL])
    service = CBLSourceService(db)

    source = run_async(service.import_url("https://example.com/events/list.cbl"))
    db.commit()

    assert source.origin == "url"
    assert source.source_url == "https://example.com/events/list.cbl"
    assert Path(source.stored_path).read_bytes() == VALID_CBL


def test_import_url_pins_connection_to_resolved_address(db, tmp_path, monkeypatch):
    """
    The actual HTTP request must target the pre-validated IP (not let httpx
    re-resolve the hostname independently), with the real hostname preserved
    via the Host header and sni_hostname extension for TLS/virtual hosting --
    this is the fix for the DNS-rebinding TOCTOU gap between the safety check
    and the real connection.
    """
    _patch_cbl_dir(monkeypatch, tmp_path)
    created_clients = _patch_fake_client(monkeypatch, chunks=[VALID_CBL])
    service = CBLSourceService(db)

    run_async(service.import_url("https://example.com/events/list.cbl"))

    assert len(created_clients) == 1
    call = created_clients[0].stream_calls[0]
    assert call["url"] == f"https://{FAKE_SAFE_ADDRESS}/events/list.cbl"
    assert call["headers"] == {"Host": "example.com"}
    assert call["extensions"] == {"sni_hostname": "example.com"}


def test_pin_url_to_address_ipv4_rewrites_host_only():
    assert _pin_url_to_address("https://example.com/path?q=1", "203.0.113.5") == "https://203.0.113.5/path?q=1"


def test_pin_url_to_address_preserves_explicit_port():
    assert _pin_url_to_address("https://example.com:8443/x", "203.0.113.5") == "https://203.0.113.5:8443/x"


def test_pin_url_to_address_ipv6_is_bracketed():
    assert _pin_url_to_address("https://example.com/x", "2001:db8::1") == "https://[2001:db8::1]/x"


def test_import_url_enforces_size_cap(db, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(cbl_source_service_module, "MAX_CBL_SIZE_BYTES", 10)
    _patch_fake_client(monkeypatch, chunks=[VALID_CBL])
    service = CBLSourceService(db)

    with pytest.raises(CBLSourceError, match="exceeds maximum size"):
        run_async(service.import_url("https://example.com/big.cbl"))

    assert db.query(CBLSource).count() == 0


def test_refresh_failure_does_not_touch_existing_reading_list(db, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)
    service = CBLSourceService(db)
    _patch_fake_client(monkeypatch, chunks=[VALID_CBL])
    source = run_async(service.import_url("https://example.com/list.cbl"))

    library = create_library_with_root(db, "cbl-refresh-lib", str(tmp_path / "lib"))
    series = Series(name="cbl-refresh-series", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()
    comic = create_comic(db, volume, library.active_root, "issue.cbz", number="1", filename="issue.cbz", page_count=1)

    reading_list = ReadingList(name="CBL Refresh List", source="cbl", source_cbl_id=source.id)
    db.add(reading_list)
    db.flush()
    db.add(ReadingListItem(reading_list_id=reading_list.id, comic_id=comic.id, position=1))
    db.commit()

    original_fingerprint = source.fingerprint

    _patch_fake_client(monkeypatch, raise_on_stream=httpx.HTTPError("network exploded"))
    refreshed = run_async(service.refresh(source.id))
    db.commit()

    assert refreshed.last_refresh_status == "failed"
    assert refreshed.fingerprint == original_fingerprint
    assert db.query(ReadingList).filter(ReadingList.id == reading_list.id).count() == 1
    assert db.query(ReadingListItem).filter(ReadingListItem.reading_list_id == reading_list.id).count() == 1


def test_refresh_success_updates_fingerprint_and_removes_old_file(db, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)
    _patch_fake_client(monkeypatch, chunks=[VALID_CBL])
    service = CBLSourceService(db)
    source = run_async(service.import_url("https://example.com/list.cbl"))
    db.commit()

    old_path = Path(source.stored_path)
    old_fingerprint = source.fingerprint
    assert old_path.exists()

    new_content = VALID_CBL.replace(b"Test List", b"Updated List")
    _patch_fake_client(monkeypatch, chunks=[new_content])

    refreshed = run_async(service.refresh(source.id))
    db.commit()

    assert refreshed.last_refresh_status == "ok"
    assert refreshed.fingerprint != old_fingerprint
    assert not old_path.exists()
    assert Path(refreshed.stored_path).read_bytes() == new_content


def test_refresh_rejects_unparseable_content_and_preserves_last_good_file(db, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)
    _patch_fake_client(monkeypatch, chunks=[VALID_CBL])
    service = CBLSourceService(db)
    source = run_async(service.import_url("https://example.com/list.cbl"))
    db.commit()

    old_path = Path(source.stored_path)
    old_fingerprint = source.fingerprint
    assert old_path.exists()

    # Passes the cheap "looks like CBL/XML" sniff (starts with <?xml, has
    # <ReadingList) but is not well-formed XML -- parse_cbl must reject it.
    broken_content = b'<?xml version="1.0"?><ReadingList><Name>Broken'
    _patch_fake_client(monkeypatch, chunks=[broken_content])

    refreshed = run_async(service.refresh(source.id))
    db.commit()

    assert refreshed.last_refresh_status == "failed"
    assert refreshed.fingerprint == old_fingerprint
    assert old_path.exists()
    assert old_path.read_bytes() == VALID_CBL


def test_rebuild_creates_reading_list_from_matched_entries(db, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)
    library = create_library_with_root(db, "cbl-rebuild-lib", str(tmp_path / "lib"))
    series = Series(name="Rebuild Series", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()
    comic = create_comic(
        db, volume, library.active_root, "rebuild-1.cbz",
        number="1", filename="rebuild-1.cbz", page_count=1,
    )
    db.commit()

    cbl_content = (
        b'<?xml version="1.0"?><ReadingList><Name>Rebuild Event</Name>'
        b'<Books><Book Series="Rebuild Series" Number="1" /></Books></ReadingList>'
    )
    service = CBLSourceService(db)
    source = service.import_upload(cbl_content, "rebuild-event.cbl")
    db.commit()

    rebuilt = service.rebuild(source.id)
    db.commit()

    assert rebuilt.entry_count == 1
    reading_list = db.query(ReadingList).filter(ReadingList.source_cbl_id == source.id).first()
    assert reading_list is not None
    assert reading_list.name == "Rebuild Event"
    assert reading_list.source == "cbl"
    items = db.query(ReadingListItem).filter(ReadingListItem.reading_list_id == reading_list.id).all()
    assert [item.comic_id for item in items] == [comic.id]


def test_rebuild_records_parse_error_without_raising(db, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)
    service = CBLSourceService(db)
    source = service.import_upload(VALID_CBL, "will-corrupt.cbl")
    db.commit()

    # Corrupt the stored file on disk after import to simulate a broken file.
    Path(source.stored_path).write_bytes(b"<ReadingList><Name>Broken")

    rebuilt = service.rebuild(source.id)
    db.commit()

    assert "error" in rebuilt.last_match_summary
    assert db.query(ReadingList).filter(ReadingList.source_cbl_id == source.id).count() == 0


def test_delete_removes_file_reading_list_and_row(db, tmp_path, monkeypatch):
    _patch_cbl_dir(monkeypatch, tmp_path)
    service = CBLSourceService(db)
    source = service.import_upload(VALID_CBL, "delete-me.cbl")
    db.commit()

    reading_list = ReadingList(name="Delete Me CBL List", source="cbl", source_cbl_id=source.id)
    db.add(reading_list)
    db.commit()

    stored_path = Path(source.stored_path)
    assert stored_path.exists()

    service.delete(source.id)

    assert not stored_path.exists()
    assert db.query(CBLSource).filter(CBLSource.id == source.id).count() == 0
    assert db.query(ReadingList).filter(ReadingList.id == reading_list.id).count() == 0
