import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from app.config import settings
from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.library_root import LibraryRoot
from app.models.series import Series
from app.models.user import User
from tests.factories import create_comic, create_library_with_root


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (40, 60), color)
    image.putpixel((0, 0), (250, 250, 250))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _write_cbz(path: Path, page_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for index in range(1, page_count + 1):
            archive.writestr(f"p{index:02d}.png", _png_bytes((index * 50, 60, 120)))


def _cover_override_page(browser_server, comic_id: int):
    session = browser_server["db_factory"]()
    try:
        return session.get(Comic, comic_id).cover_override_page
    finally:
        session.close()


@pytest.mark.browser
def test_admin_can_pick_a_cover_page_and_reset_it(page, browser_server, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cover_dir", tmp_path / "cover")
    monkeypatch.setattr(settings, "cover_override_dir", tmp_path / "cover_overrides")
    root_path = tmp_path / "cover-ui-library"
    _write_cbz(root_path / "issue.cbz", page_count=3)

    session = browser_server["db_factory"]()
    try:
        user = session.get(User, browser_server["seed"]["user_id"])
        user.is_superuser = True
        library = create_library_with_root(session, "Cover UI Library", str(root_path))
        series = Series(name="Cover UI Series", library=library)
        volume = Volume(series=series, volume_number=1)
        session.add_all([series, volume])
        session.flush()
        comic = create_comic(session, volume, library.active_root, "issue.cbz", filename="issue.cbz", number="1")
        session.commit()
        comic_id, series_id, volume_id, library_id = comic.id, series.id, volume.id, library.id
    finally:
        session.close()

    try:
        page.goto(f"{browser_server['base_url']}/comics/{comic_id}", wait_until="networkidle")
        page.get_by_role("button", name="Change Cover").click()

        dialog = page.get_by_role("dialog", name="Change Cover")
        dialog.get_by_text("Using the automatic cover.").wait_for()
        assert dialog.locator("[data-cover-page]").count() == 3
        first_page = dialog.get_by_role("button", name="Use page 1 as the cover")
        assert first_page.get_attribute("aria-pressed") == "true"
        assert dialog.get_by_role("button", name="Reset to Automatic").is_disabled()

        second_page = dialog.get_by_role("button", name="Use page 2 as the cover")
        second_page.click()
        dialog.get_by_text("Using page 2 (p02.png).").wait_for()
        assert second_page.get_attribute("aria-pressed") == "true"
        assert first_page.get_attribute("aria-pressed") == "false"
        assert _cover_override_page(browser_server, comic_id) == "p02.png"
        assert (tmp_path / "cover" / f"comic_{comic_id}.webp").is_file()

        upload_input = dialog.get_by_label("Cover image file")
        upload_input.set_input_files(
            files=[{"name": "my-cover.png", "mimeType": "image/png", "buffer": _png_bytes((20, 200, 40))}]
        )
        dialog.get_by_role("button", name="Upload").click()
        dialog.get_by_text("Using an uploaded image.").wait_for()
        assert upload_input.input_value() == ""
        assert second_page.get_attribute("aria-pressed") == "false"
        assert (tmp_path / "cover_overrides" / f"{comic_id}.png").is_file()

        dialog.get_by_role("button", name="Reset to Automatic").click()
        dialog.get_by_text("Using the automatic cover.").wait_for()
        assert first_page.get_attribute("aria-pressed") == "true"
        assert _cover_override_page(browser_server, comic_id) is None
        assert not (tmp_path / "cover_overrides" / f"{comic_id}.png").exists()

        dialog.get_by_role("button", name="Done").click()
        dialog.wait_for(state="hidden")
    finally:
        session = browser_server["db_factory"]()
        try:
            session.query(Comic).filter(Comic.id == comic_id).delete(synchronize_session=False)
            session.query(Volume).filter(Volume.id == volume_id).delete(synchronize_session=False)
            session.query(Series).filter(Series.id == series_id).delete(synchronize_session=False)
            session.query(LibraryRoot).filter(LibraryRoot.library_id == library_id).delete(synchronize_session=False)
            session.query(Library).filter(Library.id == library_id).delete(synchronize_session=False)
            user = session.get(User, browser_server["seed"]["user_id"])
            if user is not None:
                user.is_superuser = False
            session.commit()
        finally:
            session.close()


@pytest.mark.browser
def test_change_cover_dialog_sits_above_the_nav_bar(browser, browser_server, tmp_path, monkeypatch):
    """The dialog must not slide under the sticky nav on a short window."""
    monkeypatch.setattr(settings, "cover_dir", tmp_path / "cover")
    root_path = tmp_path / "cover-nav-library"
    _write_cbz(root_path / "issue.cbz", page_count=12)

    session = browser_server["db_factory"]()
    try:
        user = session.get(User, browser_server["seed"]["user_id"])
        user.is_superuser = True
        library = create_library_with_root(session, "Cover Nav Library", str(root_path))
        series = Series(name="Cover Nav Series", library=library)
        volume = Volume(series=series, volume_number=1)
        session.add_all([series, volume])
        session.flush()
        comic = create_comic(session, volume, library.active_root, "issue.cbz", filename="issue.cbz", number="1")
        session.commit()
        comic_id, series_id, volume_id, library_id = comic.id, series.id, volume.id, library.id
    finally:
        session.close()

    context = browser.new_context(viewport={"width": 1280, "height": 560})
    page = context.new_page()
    try:
        page.goto(f"{browser_server['base_url']}/comics/{comic_id}", wait_until="networkidle")
        page.get_by_role("button", name="Change Cover").click()
        dialog = page.get_by_role("dialog", name="Change Cover")
        dialog.get_by_text("Using the automatic cover.").wait_for()

        box = dialog.bounding_box()
        top_is_dialog = page.evaluate(
            """([x, y]) => !!document.elementFromPoint(x, y)?.closest('[data-cover-picker]')""",
            [box["x"] + 40, box["y"] + 8],
        )
        assert top_is_dialog, "the nav bar covers the top of the Change Cover dialog"
        nav_is_dimmed = page.evaluate(
            """() => {
                const nav = document.querySelector('nav').getBoundingClientRect();
                return !document.elementFromPoint(nav.left + nav.width / 2, nav.top + nav.height / 2)?.closest('nav');
            }"""
        )
        assert nav_is_dimmed, "the dialog backdrop should cover the nav bar"
    finally:
        context.close()
        session = browser_server["db_factory"]()
        try:
            session.query(Comic).filter(Comic.id == comic_id).delete(synchronize_session=False)
            session.query(Volume).filter(Volume.id == volume_id).delete(synchronize_session=False)
            session.query(Series).filter(Series.id == series_id).delete(synchronize_session=False)
            session.query(LibraryRoot).filter(LibraryRoot.library_id == library_id).delete(synchronize_session=False)
            session.query(Library).filter(Library.id == library_id).delete(synchronize_session=False)
            user = session.get(User, browser_server["seed"]["user_id"])
            if user is not None:
                user.is_superuser = False
            session.commit()
        finally:
            session.close()


@pytest.mark.browser
def test_change_cover_is_hidden_from_non_admins(page, browser_server):
    page.goto(f"{browser_server['base_url']}/comics/{browser_server['seed']['active_comic_id']}", wait_until="networkidle")
    page.get_by_role("button", name="Mark Read").wait_for()

    assert page.get_by_role("button", name="Change Cover").count() == 0
