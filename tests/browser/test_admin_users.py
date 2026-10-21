import pytest

from app.models.library import Library
from app.models.library_root import LibraryRoot
from app.models.user import User
from tests.factories import create_library_with_root


@pytest.mark.browser
def test_admin_add_user_library_labels_and_select_all(page, browser_server, tmp_path):
    session = browser_server["db_factory"]()
    try:
        user = session.get(User, browser_server["seed"]["user_id"])
        user.is_superuser = True
        extra_library = create_library_with_root(session, "Select All Extra Library", str(tmp_path))
        session.commit()
        extra_library_id = extra_library.id
    finally:
        session.close()

    try:
        page.goto(f"{browser_server['base_url']}/admin/users", wait_until="networkidle")
        page.get_by_role("button", name="+ Add User").click()

        library_checkbox = page.get_by_role("checkbox", name="Browser Test Library").locator("visible=true")
        extra_checkbox = page.get_by_role("checkbox", name="Select All Extra Library").locator("visible=true")
        select_all = page.locator("[data-select-all-libraries]:visible input")
        library_checkbox.wait_for()

        # Clicking the library name toggles its checkbox.
        page.locator("label:visible").filter(has_text="Browser Test Library").locator("span").click()
        assert library_checkbox.is_checked()
        assert not extra_checkbox.is_checked()
        assert select_all.evaluate("(el) => el.indeterminate") is True
        assert not select_all.is_checked()

        select_all.click()
        assert library_checkbox.is_checked()
        assert extra_checkbox.is_checked()
        assert select_all.is_checked()
        assert select_all.evaluate("(el) => el.indeterminate") is False

        select_all.click()
        assert not library_checkbox.is_checked()
        assert not extra_checkbox.is_checked()
        assert not select_all.is_checked()
    finally:
        session = browser_server["db_factory"]()
        try:
            session.query(LibraryRoot).filter(LibraryRoot.library_id == extra_library_id).delete(
                synchronize_session=False
            )
            session.query(Library).filter(Library.id == extra_library_id).delete(synchronize_session=False)
            session.commit()
        finally:
            session.close()
