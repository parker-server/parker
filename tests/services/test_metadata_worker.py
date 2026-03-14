from pathlib import Path

import app.services.workers.metadata_worker as metadata_worker_module


def _write_comic_file(tmp_path: Path, name: str = "issue.cbz") -> Path:
    file_path = tmp_path / name
    file_path.write_bytes(b"dummy")
    return file_path


def test_metadata_worker_success_overrides_xml_page_count(monkeypatch, tmp_path):
    file_path = _write_comic_file(tmp_path)

    class ArchiveWithXml:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_pages(self):
            return ["p1", "p2", "p3"]

        def get_comicinfo(self):
            return "<xml/>"

    monkeypatch.setattr("app.services.archive.ComicArchive", ArchiveWithXml)
    monkeypatch.setattr("app.services.metadata.parse_comicinfo", lambda _xml: {"title": "Parsed", "page_count": 999})

    payload = metadata_worker_module.metadata_worker(str(file_path))

    assert payload["error"] is False
    assert payload["file_path"] == str(file_path)
    assert payload["metadata"]["title"] == "Parsed"
    assert payload["metadata"]["raw_metadata"]["page_count"] == 999
    assert payload["metadata"]["page_count"] == 3
    assert payload["size"] == file_path.stat().st_size


def test_metadata_worker_returns_error_when_pages_missing(monkeypatch, tmp_path):
    file_path = _write_comic_file(tmp_path, name="no-pages.cbz")

    class ArchiveNoPages:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_pages(self):
            return []

        def get_comicinfo(self):
            return "<xml/>"

    monkeypatch.setattr("app.services.archive.ComicArchive", ArchiveNoPages)

    payload = metadata_worker_module.metadata_worker(str(file_path))

    assert payload == {
        "file_path": str(file_path),
        "error": True,
        "message": "No valid pages found (archive unreadable)",
    }


def test_metadata_worker_returns_error_when_comicinfo_missing(monkeypatch, tmp_path):
    file_path = _write_comic_file(tmp_path, name="missing-xml.cbz")

    class ArchiveNoXml:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_pages(self):
            return ["p1"]

        def get_comicinfo(self):
            return None

    monkeypatch.setattr("app.services.archive.ComicArchive", ArchiveNoXml)

    payload = metadata_worker_module.metadata_worker(str(file_path))

    assert payload == {
        "file_path": str(file_path),
        "error": True,
        "message": "Missing ComicInfo.xml",
    }


def test_metadata_worker_returns_exception_message(monkeypatch, tmp_path):
    file_path = _write_comic_file(tmp_path, name="boom.cbz")

    class ArchiveBoom:
        def __init__(self, _path):
            raise RuntimeError("archive exploded")

    monkeypatch.setattr("app.services.archive.ComicArchive", ArchiveBoom)

    payload = metadata_worker_module.metadata_worker(str(file_path))

    assert payload["file_path"] == str(file_path)
    assert payload["error"] is True
    assert "archive exploded" in payload["message"]
