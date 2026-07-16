import pytest
from sqlalchemy import select

from app.models.reading_progress import ReadingProgress


@pytest.mark.browser
def test_reader_page_loads_and_exposes_reader_controls(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/reader/{seed['active_comic_id']}", wait_until="networkidle")

    page.wait_for_selector(".reader-container")
    page.wait_for_selector("img.reader-page")

    title_text = page.locator(".reader-toolbar .font-bold.text-white").first
    assert title_text.text_content() == f"{seed['series_name']} #{seed['active_comic_number']}"
    subtitle_text = page.locator(".reader-toolbar .text-xs.text-gray-400.truncate").first
    assert subtitle_text.text_content() == seed["active_comic_title"]

    page.locator(".nav-zone.center").click()
    page.wait_for_timeout(150)

    settings_button = page.locator("button[title='Settings']")
    settings_button.click()
    page.wait_for_selector(".settings-panel")
    assert page.locator(".settings-panel").is_visible()
    assert page.locator("text=Double Page (d)").is_visible()
    assert page.locator("text=Reduce Data Usage").is_visible()


@pytest.mark.browser
def test_reader_keyboard_navigation_persists_progress(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/reader/{seed['active_comic_id']}", wait_until="networkidle")

    page.wait_for_selector(".reader-container")
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(300)

    current_page_text = page.locator(".reader-controls .text-white.font-bold").first.text_content()
    assert current_page_text == "2"

    session = browser_server["db_factory"]()
    try:
        progress = session.scalar(
            select(ReadingProgress).where(
                ReadingProgress.user_id == seed["user_id"],
                ReadingProgress.comic_id == seed["active_comic_id"],
            )
        )
    finally:
        session.close()

    assert progress is not None
    assert progress.current_page == 1
    assert progress.completed is False


@pytest.mark.browser
def test_reader_marks_comic_complete_on_last_page(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/reader/{seed['active_comic_id']}", wait_until="networkidle")

    page.wait_for_selector(".reader-container")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(400)

    current_page_text = page.locator(".reader-controls .text-white.font-bold").first.text_content()
    assert current_page_text == "3"

    session = browser_server["db_factory"]()
    try:
        progress = session.scalar(
            select(ReadingProgress).where(
                ReadingProgress.user_id == seed["user_id"],
                ReadingProgress.comic_id == seed["active_comic_id"],
            )
        )
    finally:
        session.close()

    assert progress is not None
    assert progress.current_page == 2
    assert progress.completed is True
