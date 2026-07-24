import pytest

from app.models.comic import Comic, Volume
from app.models.library_root import LibraryRoot
from app.models.series import Series
from app.services.library_relocation import (
    LibraryRelocationError,
    NO_RELOCATION_MATCHES_MESSAGE,
    confirm_library_root_relocation,
    preview_library_root_relocation,
)
from tests.factories import create_comic, create_library_with_root


def _create_volume(db, library):
    series = Series(name=f"{library.name} Series", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()
    return volume


def _write_file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"comic")


def test_preview_library_root_relocation_counts_matched_missing_and_new_files(db, tmp_path):
    current_root_path = tmp_path / "current"
    proposed_root_path = tmp_path / "proposed"
    current_root_path.mkdir()
    proposed_root_path.mkdir()

    library = create_library_with_root(db, "Relocation Preview", str(current_root_path))
    root = library.active_root
    volume = _create_volume(db, library)

    create_comic(db, volume, root, "Alpha/one.cbz", filename="one.cbz")
    create_comic(db, volume, root, "Beta/two.cbz", filename="two.cbz")
    create_comic(db, volume, root, "Gamma/three.cbr", filename="three.cbr")
    db.commit()

    _write_file(proposed_root_path / "Alpha" / "one.cbz")
    _write_file(proposed_root_path / "Gamma" / "three.cbr")
    _write_file(proposed_root_path / "New" / "four.cbz")
    _write_file(proposed_root_path / "ignored.txt")

    preview = preview_library_root_relocation(
        db,
        library=library,
        proposed_path=str(proposed_root_path),
    )

    assert preview.library_id == library.id
    assert preview.root_id == root.id
    assert preview.current_path == str(current_root_path)
    assert preview.proposed_path == proposed_root_path.resolve().as_posix()
    assert preview.total_existing == 3
    assert preview.total_scanned == 3
    assert preview.matched_count == 2
    assert preview.missing_count == 1
    assert preview.new_count == 1
    assert preview.confirm_blocked is False
    assert preview.confirm_blocked_reason is None
    assert [sample.relative_path for sample in preview.matched_samples] == [
        "Alpha/one.cbz",
        "Gamma/three.cbr",
    ]
    assert [sample.relative_path for sample in preview.missing_samples] == ["Beta/two.cbz"]
    assert [sample.relative_path for sample in preview.new_samples] == ["New/four.cbz"]

    db.refresh(root)
    assert root.path == str(current_root_path)


def test_preview_library_root_relocation_requires_root_id_for_multiple_active_roots(db, tmp_path):
    first_root_path = tmp_path / "first"
    second_root_path = tmp_path / "second"
    proposed_root_path = tmp_path / "proposed"
    first_root_path.mkdir()
    second_root_path.mkdir()
    proposed_root_path.mkdir()

    library = create_library_with_root(db, "Multi Root Preview", str(first_root_path))
    db.add(LibraryRoot(library_id=library.id, path=str(second_root_path), is_active=True))
    db.commit()

    with pytest.raises(LibraryRelocationError, match="root_id is required"):
        preview_library_root_relocation(
            db,
            library=library,
            proposed_path=str(proposed_root_path),
        )


def test_preview_library_root_relocation_accepts_explicit_root_id(db, tmp_path):
    first_root_path = tmp_path / "first"
    second_root_path = tmp_path / "second"
    proposed_root_path = tmp_path / "proposed"
    first_root_path.mkdir()
    second_root_path.mkdir()
    proposed_root_path.mkdir()

    library = create_library_with_root(db, "Explicit Root Preview", str(first_root_path))
    second_root = LibraryRoot(library_id=library.id, path=str(second_root_path), is_active=True)
    db.add(second_root)
    db.commit()

    preview = preview_library_root_relocation(
        db,
        library=library,
        root_id=second_root.id,
        proposed_path=str(proposed_root_path),
    )

    assert preview.root_id == second_root.id
    assert preview.current_path == str(second_root_path)


def test_preview_library_root_relocation_rejects_overlap_with_sibling_root(db, tmp_path):
    first_root_path = tmp_path / "first"
    second_root_path = tmp_path / "second"
    first_root_path.mkdir()
    second_root_path.mkdir()

    library = create_library_with_root(db, "Sibling Root Preview", str(first_root_path))
    first_root_id = library.active_root.id
    second_root = LibraryRoot(library_id=library.id, path=str(second_root_path), is_active=True)
    db.add(second_root)
    db.commit()

    with pytest.raises(LibraryRelocationError, match="overlaps with existing root"):
        preview_library_root_relocation(
            db,
            library=library,
            root_id=first_root_id,
            proposed_path=str(second_root_path),
        )


def test_preview_library_root_relocation_rejects_current_root_child(db, tmp_path):
    current_root_path = tmp_path / "current"
    proposed_root_path = current_root_path / "nested"
    proposed_root_path.mkdir(parents=True)

    library = create_library_with_root(db, "Nested Root Preview", str(current_root_path))
    db.commit()

    with pytest.raises(LibraryRelocationError, match="current root path"):
        preview_library_root_relocation(
            db,
            library=library,
            proposed_path=str(proposed_root_path),
        )


def test_preview_library_root_relocation_compares_resolved_current_root_path(db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    current_root_path = tmp_path / "relative-current"
    current_root_path.mkdir()

    library = create_library_with_root(db, "Relative Current Root Preview", "relative-current")
    db.commit()

    with pytest.raises(LibraryRelocationError, match="different from the current root path"):
        preview_library_root_relocation(
            db,
            library=library,
            proposed_path=str(current_root_path.resolve()),
        )


def test_confirm_library_root_relocation_updates_root_and_preserves_comic_identity(db, tmp_path):
    current_root_path = tmp_path / "confirm-current"
    proposed_root_path = tmp_path / "confirm-proposed"
    current_root_path.mkdir()
    proposed_root_path.mkdir()

    library = create_library_with_root(db, "Confirm Relocation", str(current_root_path))
    root = library.active_root
    volume = _create_volume(db, library)

    matched = create_comic(db, volume, root, "Alpha/one.cbz", filename="one.cbz")
    missing = create_comic(db, volume, root, "Beta/two.cbz", filename="two.cbz")
    db.commit()

    root_id = root.id
    matched_id = matched.id
    missing_id = missing.id

    _write_file(proposed_root_path / "Alpha" / "one.cbz")
    _write_file(proposed_root_path / "Extra" / "three.cbz")

    confirmation = confirm_library_root_relocation(
        db,
        library=library,
        proposed_path=str(proposed_root_path),
    )

    payload = confirmation.to_dict()
    assert payload["relocated"] is True
    assert payload["previous_path"] == str(current_root_path)
    assert payload["current_path"] == proposed_root_path.resolve().as_posix()
    assert payload["proposed_path"] == proposed_root_path.resolve().as_posix()
    assert payload["matched_count"] == 1
    assert payload["missing_count"] == 1
    assert payload["new_count"] == 1
    assert payload["scan_recommended"] is True
    assert payload["scan_reasons"] == [
        "Verify relocated archives and refresh metadata if files changed",
        "Reconcile existing comics that were missing at the new root",
        "Import new archive files found at the new root",
    ]

    refreshed_root = db.get(LibraryRoot, root_id)
    assert refreshed_root.path == proposed_root_path.resolve().as_posix()

    refreshed_matched = db.get(Comic, matched_id)
    refreshed_missing = db.get(Comic, missing_id)
    assert refreshed_matched is not None
    assert refreshed_missing is not None
    assert refreshed_matched.library_root_id == root_id
    assert refreshed_missing.library_root_id == root_id
    assert refreshed_matched.relative_path == "Alpha/one.cbz"
    assert refreshed_missing.relative_path == "Beta/two.cbz"


def test_confirm_library_root_relocation_rejects_when_no_existing_files_match(db, tmp_path):
    current_root_path = tmp_path / "all-missing-current"
    proposed_root_path = tmp_path / "all-missing-proposed"
    current_root_path.mkdir()
    proposed_root_path.mkdir()

    library = create_library_with_root(db, "All Missing Relocation", str(current_root_path))
    root = library.active_root
    volume = _create_volume(db, library)
    create_comic(db, volume, root, "Alpha/one.cbz", filename="one.cbz")
    create_comic(db, volume, root, "Beta/two.cbz", filename="two.cbz")
    db.commit()

    preview = preview_library_root_relocation(
        db,
        library=library,
        proposed_path=str(proposed_root_path),
    )

    assert preview.total_existing == 2
    assert preview.matched_count == 0
    assert preview.missing_count == 2
    assert preview.confirm_blocked is True
    assert preview.confirm_blocked_reason == NO_RELOCATION_MATCHES_MESSAGE

    with pytest.raises(LibraryRelocationError, match="No existing comics were found"):
        confirm_library_root_relocation(
            db,
            library=library,
            proposed_path=str(proposed_root_path),
        )

    db.refresh(root)
    assert root.path == str(current_root_path)


def test_confirm_library_root_relocation_allows_empty_library(db, tmp_path):
    current_root_path = tmp_path / "empty-current"
    proposed_root_path = tmp_path / "empty-proposed"
    current_root_path.mkdir()
    proposed_root_path.mkdir()

    library = create_library_with_root(db, "Empty Relocation", str(current_root_path))
    root = library.active_root
    root_id = root.id
    db.commit()

    confirmation = confirm_library_root_relocation(
        db,
        library=library,
        proposed_path=str(proposed_root_path),
    )

    assert confirmation.preview.total_existing == 0
    assert confirmation.preview.confirm_blocked is False

    refreshed_root = db.get(LibraryRoot, root_id)
    assert refreshed_root.path == proposed_root_path.resolve().as_posix()


def test_confirm_library_root_relocation_reuses_preview_validation(db, tmp_path):
    first_root_path = tmp_path / "confirm-first"
    second_root_path = tmp_path / "confirm-second"
    first_root_path.mkdir()
    second_root_path.mkdir()

    library = create_library_with_root(db, "Confirm Sibling Root", str(first_root_path))
    first_root_id = library.active_root.id
    db.add(LibraryRoot(library_id=library.id, path=str(second_root_path), is_active=True))
    db.commit()

    with pytest.raises(LibraryRelocationError, match="overlaps with existing root"):
        confirm_library_root_relocation(
            db,
            library=library,
            root_id=first_root_id,
            proposed_path=str(second_root_path),
        )

    assert db.get(LibraryRoot, first_root_id).path == str(first_root_path)
