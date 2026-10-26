import csv
import zipfile
from pathlib import Path

from app.services.cover_audit import audit_pages, changed_covers, run_audit


def test_audit_pages_does_not_flag_plain_first_page_cover():
    folder = "Fantastic Four Unplugged #1 (September 1995-Marvel) c2c (Comixbear-DCP)/"
    audit = audit_pages([
        f"{folder}Fantastic Four Unplugged #1 {{Comixbear}}-01.jpg",
        f"{folder}Fantastic Four Unplugged #1 {{Comixbear}}-02.jpg",
        f"{folder}zzz A Comixbear Scan.jpg",
    ])

    assert audit.flags == ()
    assert audit.reason == "interior"


def test_audit_pages_flags_cover_moved_ahead_of_plain_first_page():
    audit = audit_pages(["cover.jpg", "01.jpg", "02.jpg"])

    assert audit.flags == ("reordered",)
    assert audit.chosen_cover == "cover.jpg"
    assert audit.plain_first_page == "01.jpg"
    assert audit.reason == "explicit_cover_token"


def test_audit_pages_flags_multiple_cover_candidates():
    audit = audit_pages(["cover.jpg", "fc.jpg", "01.jpg"])

    assert "multiple_cover_candidates" in audit.flags


def test_audit_pages_flags_cover_in_stray_folder():
    audit = audit_pages(["aaa tag.jpg", "issue/01.jpg", "issue/02.jpg", "issue/03.jpg"])

    assert audit.flags == ("cover_in_stray_folder",)


def test_audit_pages_ignores_scanner_tag_outside_cover_folder():
    audit = audit_pages(["issue/01.jpg", "issue/02.jpg", "issue/03.jpg", "xx tag.jpg"])

    assert audit.flags == ()


def test_audit_pages_ignores_collection_with_front_matter_folder():
    audit = audit_pages([
        "collection/000/dcf_strange_0003.jpg",
        "collection/000/dcf_strange_0004.jpg",
        "collection/000/dcf_strange_0005.jpg",
        "collection/001 Issue #1/01.jpg",
        "collection/001 Issue #1/02.jpg",
        "collection/002 Issue #2/01.jpg",
    ])

    assert audit.flags == ()


def test_audit_pages_flags_archive_without_pages():
    assert audit_pages([]).flags == ("no_pages",)


def _write_cbz(path: Path, names: list[str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, b"image")


def test_run_audit_writes_only_flagged_archives(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    _write_cbz(library / "clean.cbz", ["01.jpg", "02.jpg"])
    _write_cbz(library / "reordered.cbz", ["01.jpg", "cover.jpg"])
    (library / "broken.cbr").write_bytes(b"not an archive")
    (library / "notes.txt").write_text("ignored")
    output = tmp_path / "report" / "cover_audit.csv"

    flagged = run_audit([library], output)

    with output.open(newline="", encoding="utf-8-sig") as report:
        rows = list(csv.DictReader(report))

    assert flagged == 2
    assert [Path(row["path"]).name for row in rows] == ["broken.cbr", "reordered.cbz"]
    assert rows[0]["flags"] == "unreadable"
    assert rows[1]["flags"] == "reordered"
    assert rows[1]["chosen_cover"] == "cover.jpg"
    assert rows[1]["page_count"] == "2"


def test_changed_covers_ignores_added_and_removed_archives():
    before = {"a.cbz": "01.jpg", "b.cbz": "01.jpg", "removed.cbz": "01.jpg"}
    after = {"a.cbz": "01.jpg", "b.cbz": "cover.jpg", "added.cbz": "01.jpg"}

    assert changed_covers(before, after) == [("b.cbz", "01.jpg", "cover.jpg")]


def test_run_audit_compares_covers_against_snapshot(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    _write_cbz(library / "same.cbz", ["01.jpg", "02.jpg"])
    _write_cbz(library / "moved.cbz", ["01.jpg", "02.jpg"])
    snapshot = tmp_path / "covers_before.csv"
    output = tmp_path / "cover_audit.csv"

    run_audit([library], output, snapshot=snapshot)
    _write_cbz(library / "moved.cbz", ["01.jpg", "02.jpg", "cover.jpg"])
    run_audit([library], output, compare=snapshot)

    with (tmp_path / "cover_changes.csv").open(newline="", encoding="utf-8-sig") as changes_file:
        changes = list(csv.DictReader(changes_file))

    assert [Path(row["path"]).name for row in changes] == ["moved.cbz"]
    assert changes[0]["before_cover"] == "01.jpg"
    assert changes[0]["after_cover"] == "cover.jpg"
