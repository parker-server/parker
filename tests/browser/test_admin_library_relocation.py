from pathlib import Path

import pytest

from app.config import settings
from app.models.comic import Volume
from app.models.library_root import LibraryRoot
from app.models.series import Series
from app.models.user import User
from tests.factories import create_comic, create_library_with_root


def _write_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"comic")


@pytest.mark.browser
def test_admin_library_relocation_preview_and_confirm_flow(page, browser_server, tmp_path):
    current_root = tmp_path / "relocation-ui-current"
    proposed_root = tmp_path / "relocation-ui-proposed"
    current_root.mkdir()
    proposed_root.mkdir()

    session = browser_server["db_factory"]()
    try:
        user = session.get(User, browser_server["seed"]["user_id"])
        user.is_superuser = True

        library = create_library_with_root(session, "Relocation UI Library", str(current_root))
        root = library.active_root
        series = Series(name="Relocation UI Series", library=library)
        volume = Volume(series=series, volume_number=1)
        session.add_all([series, volume])
        session.flush()
        create_comic(session, volume, root, "Alpha/one.cbz", filename="one.cbz")
        create_comic(session, volume, root, "Beta/two.cbz", filename="two.cbz")
        session.commit()
        root_id = root.id
    finally:
        session.close()

    _write_file(proposed_root / "Alpha" / "one.cbz")
    _write_file(proposed_root / "Extra" / "three.cbz")

    original_comics_path = settings.comics_path
    settings.comics_path = tmp_path
    try:
        page.goto(f"{browser_server['base_url']}/admin/libraries", wait_until="networkidle")

        row = page.get_by_role("row").filter(has_text="Relocation UI Library").first
        row.wait_for()
        row.get_by_role("button", name="Roots").click()
        page.get_by_role("heading", name="Library Roots").wait_for()
        page.get_by_test_id("library-root-row").first.get_by_role("button", name="Relocate").click()

        relocation_modal = page.locator('[x-show="showRelocationModal"]')
        relocation_modal.get_by_role("heading", name="Relocate Library Path").wait_for()
        relocation_modal.get_by_role("button", name="Browse").click()
        relocation_modal.get_by_role("button", name="../").click()
        relocation_modal.get_by_role("button", name=f"/{proposed_root.name}").click()
        relocation_modal.get_by_role("button", name="Use This Folder").click()
        path_input = relocation_modal.get_by_placeholder("/comics/relocated")
        path_input.wait_for(state="visible")
        assert path_input.input_value() == proposed_root.resolve().as_posix()
        relocation_modal.get_by_role("button", name="Preview").click()

        relocation_modal.get_by_text("Matched Files").wait_for()
        relocation_modal.get_by_text("Alpha/one.cbz").wait_for()
        relocation_modal.get_by_text("Beta/two.cbz").wait_for()
        relocation_modal.get_by_text("Extra/three.cbz").wait_for()

        relocation_modal.get_by_role("button", name="Confirm Relocation").click()
        page.get_by_role("heading", name="Relocate Relocation UI Library?").wait_for()
        page.locator('[x-data="globalDialog()"]').get_by_role("button", name="Relocate").click()

        page.get_by_role("heading", name="Scan Recommended").wait_for()
        page.locator('[x-data="globalDialog()"]').get_by_role("button", name="Not Now").click()
        relocation_modal.get_by_text("Relocation complete. Scan recommended.").wait_for()
        relocation_modal.get_by_role("button", name="Run Force Scan").wait_for()
        assert path_input.input_value() == proposed_root.resolve().as_posix()

        session = browser_server["db_factory"]()
        try:
            relocated_root = session.get(LibraryRoot, root_id)
            assert relocated_root.path == proposed_root.resolve().as_posix()
        finally:
            session.close()
    finally:
        settings.comics_path = original_comics_path
        session = browser_server["db_factory"]()
        try:
            user = session.get(User, browser_server["seed"]["user_id"])
            if user is not None:
                user.is_superuser = False
                session.commit()
        finally:
            session.close()


@pytest.mark.browser
def test_admin_library_root_lifecycle_flow(page, browser_server, tmp_path):
    current_root = tmp_path / "roots-ui-current"
    archive_root = tmp_path / "roots-ui-archive"
    current_root.mkdir()
    archive_root.mkdir()

    session = browser_server["db_factory"]()
    try:
        user = session.get(User, browser_server["seed"]["user_id"])
        user.is_superuser = True
        library = create_library_with_root(session, "Roots UI Library", str(current_root))
        session.commit()
        library_id = library.id
    finally:
        session.close()

    original_comics_path = settings.comics_path
    settings.comics_path = tmp_path
    browser_errors = []
    page.on("pageerror", lambda exc: browser_errors.append(str(exc)))
    page.on(
        "console",
        lambda msg: browser_errors.append(msg.text)
        if "Cannot read properties of undefined" in msg.text
        else None,
    )
    try:
        page.goto(f"{browser_server['base_url']}/admin/libraries", wait_until="networkidle")

        row = page.get_by_role("row").filter(has_text="Roots UI Library").first
        row.wait_for()
        row.get_by_role("button", name="Roots").click()

        roots_modal = page.locator('[x-show="showRootsModal"]')
        roots_modal.get_by_role("heading", name="Library Roots").wait_for()
        archive_path = archive_root.resolve().as_posix()
        roots_modal.get_by_role("button", name="Browse").click()
        roots_modal.get_by_role("button", name="../").click()
        roots_modal.get_by_role("button", name=f"/{current_root.name}").click()
        roots_modal.get_by_role("button", name="../").click()
        roots_modal.get_by_role("button", name=f"/{archive_root.name}").click()
        roots_modal.get_by_role("button", name="Use This Folder").click()
        assert roots_modal.get_by_placeholder("/comics/archive").input_value() == archive_path
        assert browser_errors == []

        roots_modal.get_by_role("button", name="Add Root").click()

        archive_row = page.get_by_test_id("library-root-row").filter(has_text=archive_path)
        archive_row.wait_for()
        archive_row.get_by_role("button", name="Disable").click()
        archive_row.get_by_text("Disabled").wait_for()

        archive_row.get_by_role("button", name="Remove").click()
        page.get_by_role("heading", name="Remove Root?").wait_for()
        page.locator('[x-data="globalDialog()"]').get_by_role("button", name="Remove Root").click()
        page.get_by_text(archive_path).wait_for(state="detached")

        current_row = page.get_by_test_id("library-root-row").filter(has_text=str(current_root))
        current_row.get_by_role("button", name="Disable").click()
        dialog = page.locator('[x-data="globalDialog()"]')
        dialog.get_by_role("heading", name="Disable Last Active Root?").wait_for()
        dialog.get_by_text("Existing comics will remain visible and readable if their files are reachable").wait_for()
        dialog.get_by_role("button", name="Cancel").click()
        current_row.get_by_text("Active").wait_for()

        session = browser_server["db_factory"]()
        try:
            roots = (
                session.query(LibraryRoot)
                .filter(LibraryRoot.library_id == library_id)
                .order_by(LibraryRoot.id)
                .all()
            )
            assert len(roots) == 1
            assert roots[0].path == str(current_root)
            assert roots[0].is_active is True
        finally:
            session.close()
    finally:
        settings.comics_path = original_comics_path
        session = browser_server["db_factory"]()
        try:
            user = session.get(User, browser_server["seed"]["user_id"])
            if user is not None:
                user.is_superuser = False
                session.commit()
        finally:
            session.close()
