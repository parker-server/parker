from pathlib import Path

import pytest

import app.services.cbl_source_service as cbl_source_service_module
from app.models.user import User

FIXTURE_CBL = Path(__file__).resolve().parent.parent / "fixtures" / "cbl" / "valid.cbl"


@pytest.mark.browser
def test_admin_cbl_sources_upload_and_delete_flow(page, browser_server, tmp_path, monkeypatch):
    monkeypatch.setattr(cbl_source_service_module.settings, "cbl_dir", tmp_path / "cbl")

    session = browser_server["db_factory"]()
    try:
        user = session.get(User, browser_server["seed"]["user_id"])
        user.is_superuser = True
        session.commit()
    finally:
        session.close()

    try:
        page.goto(f"{browser_server['base_url']}/admin/cbl-sources", wait_until="networkidle")

        page.get_by_role("button", name="+ Upload CBL").click()
        modal = page.locator('[x-show="showUploadModal"]')
        modal.get_by_role("heading", name="Upload CBL File").wait_for()

        modal.locator('input[type="file"]').set_input_files(str(FIXTURE_CBL))
        modal.get_by_role("button", name="Upload").click()

        row = page.get_by_role("row").filter(has_text="valid")
        row.wait_for()
        row.get_by_text("upload").wait_for()
        row.get_by_text("Infinity Gauntlet").wait_for()

        row.get_by_role("button", name="Rename").click()
        rename_modal = page.locator('[x-show="showRenameModal"]')
        rename_modal.get_by_role("heading", name="Rename Reading List").wait_for()
        name_input = rename_modal.locator('input[type="text"]')
        assert name_input.input_value() == "Infinity Gauntlet"
        name_input.fill("My Renamed Event")
        rename_modal.get_by_role("button", name="Save").click()

        row.get_by_text("My Renamed Event").wait_for()

        row.get_by_role("button", name="Delete").click()
        page.get_by_role("heading", name='Delete "valid"?').wait_for()
        page.locator('[x-data="globalDialog()"]').get_by_role("button", name="Delete").click()

        page.get_by_text("No CBL sources yet.").wait_for()
    finally:
        session = browser_server["db_factory"]()
        try:
            user = session.get(User, browser_server["seed"]["user_id"])
            if user is not None:
                user.is_superuser = False
                session.commit()
        finally:
            session.close()
