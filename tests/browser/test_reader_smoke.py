import pytest
from sqlalchemy import select

from app.models.reading_progress import ReadingProgress

TALL_SCROLL_PAGE_BYTES = b"""
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="3200" viewBox="0 0 1200 3200">
  <rect width="1200" height="3200" fill="#111827"/>
  <rect x="72" y="72" width="1056" height="3056" rx="48" fill="#2563eb"/>
  <rect x="132" y="132" width="936" height="720" rx="32" fill="#0f172a"/>
  <text x="600" y="560" fill="#e5e7eb" font-size="120" text-anchor="middle" font-family="Arial, sans-serif">
    Long View Test
  </text>
</svg>
"""


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


@pytest.mark.browser
def test_reader_long_view_toggle_tracks_scroll_progress_and_persists_mode(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/reader/{seed['active_comic_id']}", wait_until="networkidle")

    page.wait_for_selector(".reader-container")
    page.locator(".nav-zone.center").click()
    page.locator("button[title='Settings']").click()
    page.get_by_role("button", name="Long View").click()

    page.wait_for_function(
        """
        () => {
            const reader = document.querySelector('.reader-container');
            const state = reader?._x_dataStack?.[0];
            return reader?.classList.contains('scroll-mode')
                && state?.readingMode === 'scroll'
                && state?.meta?.page_count > 0
                && document.querySelectorAll('[data-scroll-page-index]').length > 0;
        }
        """
    )


@pytest.mark.browser
def test_reader_long_view_settings_panel_stays_visible_after_scrolling(page, browser_server, monkeypatch):
    monkeypatch.setattr(
        "app.api.reader.ImageService.get_page_image",
        lambda self, file_path, page_index, sharpen=False, grayscale=False, transcode_webp=False: (
            TALL_SCROLL_PAGE_BYTES,
            False,
            "image/svg+xml",
        ),
    )

    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/reader/{seed['active_comic_id']}", wait_until="networkidle")

    page.wait_for_selector(".reader-container")
    page.locator(".nav-zone.center").click()
    page.locator("button[title='Settings']").click()
    page.get_by_role("button", name="Long View").click()

    page.wait_for_function(
        """
        () => {
            const reader = document.querySelector('.reader-container');
            return reader?.classList.contains('scroll-mode') && reader.scrollHeight > reader.clientHeight;
        }
        """
    )

    page.evaluate(
        """
        () => {
            const reader = document.querySelector('.reader-container');
            reader.scrollTop = 1200;
        }
        """
    )
    page.wait_for_timeout(150)

    page.locator("button[title='Settings']").click()
    page.wait_for_function(
        """
        () => {
            const panel = document.querySelector('.settings-panel');
            const reader = document.querySelector('.reader-container');
            const state = reader?._x_dataStack?.[0];
            if (!panel || !state?.showSettings) {
                return false;
            }

            const rect = panel.getBoundingClientRect();
            return rect.top >= 0 && rect.top < window.innerHeight && rect.right <= window.innerWidth;
        }
        """
    )

    page.reload(wait_until="networkidle")
    page.wait_for_function(
        """
        () => {
            const reader = document.querySelector('.reader-container');
            const state = reader?._x_dataStack?.[0];
            return reader?.classList.contains('scroll-mode')
                && state?.readingMode === 'scroll'
                && state?.meta?.page_count > 0
                && document.querySelectorAll('[data-scroll-page-index]').length > 0;
        }
        """
    )
