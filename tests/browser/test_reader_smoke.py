import pytest
from sqlalchemy import select

from app.models.bookmark import Bookmark
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
def test_reader_paged_navigation_arrows_change_pages(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/reader/{seed['active_comic_id']}", wait_until="networkidle")

    page.wait_for_selector(".reader-container")
    page.locator(".nav-zone.center").click()

    next_page_button = page.locator(".reader-controls button").nth(2)
    next_page_button.click()
    page.wait_for_timeout(300)

    current_page_text = page.locator(".reader-controls .text-white.font-bold").first.text_content()
    assert current_page_text == "2"

    prev_page_button = page.locator(".reader-controls button").nth(1)
    prev_page_button.click()
    page.wait_for_timeout(300)

    current_page_text = page.locator(".reader-controls .text-white.font-bold").first.text_content()
    assert current_page_text == "1"


@pytest.mark.browser
def test_reader_paged_scrubber_moves_to_selected_page_and_updates_progress(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/reader/{seed['active_comic_id']}", wait_until="networkidle")

    page.wait_for_selector(".reader-container")
    page.locator(".nav-zone.center").click()

    scrubber = page.locator("input.scrubber")
    scrubber_box = scrubber.bounding_box()
    assert scrubber_box is not None
    scrubber.click(position={"x": scrubber_box["width"] * 0.92, "y": scrubber_box["height"] / 2})
    page.wait_for_timeout(500)

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
def test_reader_bookmarks_save_jump_and_delete(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/reader/{seed['active_comic_id']}", wait_until="networkidle")

    page.wait_for_selector(".reader-container")
    page.locator(".nav-zone.center").click()
    page.locator("[data-bookmarks-toggle]").click()
    page.wait_for_selector("[data-bookmarks-modal]")

    label_input = page.locator("[data-bookmarks-modal] input[type='text']")
    label_input.fill("Intro beat")
    page.locator("[data-save-bookmark]").click()
    page.wait_for_selector("[data-bookmark-item]")

    bookmark_item = page.locator("[data-bookmark-item]").first
    assert bookmark_item.locator("text=Page 1").is_visible()
    assert bookmark_item.locator("text=Intro beat").is_visible()

    page.locator("[data-close-bookmarks]").click()
    page.locator(".reader-controls button").nth(2).click()
    page.wait_for_timeout(300)
    assert page.locator(".reader-controls .text-white.font-bold").first.text_content() == "2"

    page.locator("[data-bookmarks-toggle]").click()
    page.wait_for_selector("[data-bookmarks-modal]")
    page.locator("[data-bookmark-item]").first.locator("button").first.click()
    page.wait_for_timeout(300)
    assert page.locator(".reader-controls .text-white.font-bold").first.text_content() == "1"

    session = browser_server["db_factory"]()
    try:
        bookmark = session.scalar(
            select(Bookmark).where(
                Bookmark.user_id == seed["user_id"],
                Bookmark.comic_id == seed["active_comic_id"],
            )
        )
    finally:
        session.close()

    assert bookmark is not None
    assert bookmark.page_index == 0
    assert bookmark.label == "Intro beat"

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

    page.locator("[data-bookmarks-toggle]").click()
    page.wait_for_selector("[data-bookmarks-modal]")
    page.locator("[data-delete-bookmark]").click()
    page.wait_for_timeout(300)
    assert page.locator("[data-bookmark-item]").count() == 0


@pytest.mark.browser
def test_reader_double_page_mode_advances_as_spreads(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/reader/{seed['active_comic_id']}", wait_until="networkidle")

    page.wait_for_selector(".reader-container")
    page.locator(".nav-zone.center").click()
    page.locator("button[title='Settings']").click()
    page.locator("label:text('Double Page (d)')").locator("..").get_by_role("button").click()
    page.wait_for_timeout(250)

    page.wait_for_function(
        """
        () => {
            const reader = document.querySelector('.reader-container');
            const state = reader?._x_dataStack?.[0];
            return state?.viewMode === 'double' && document.querySelectorAll('img.reader-page').length === 1;
        }
        """
    )

    next_page_button = page.locator(".reader-controls button").nth(2)
    next_page_button.click()
    page.wait_for_timeout(300)

    current_page_text = page.locator(".reader-controls .text-white.font-bold").first.text_content()
    assert current_page_text == "2"
    assert page.locator("img.reader-page").count() == 2

    prev_page_button = page.locator(".reader-controls button").nth(1)
    prev_page_button.click()
    page.wait_for_timeout(300)

    current_page_text = page.locator(".reader-controls .text-white.font-bold").first.text_content()
    assert current_page_text == "1"
    assert page.locator("img.reader-page").count() == 1


@pytest.mark.browser
def test_reader_double_page_mode_persists_per_comic_without_leaking_to_other_books(page, browser_server):
    seed = browser_server["seed"]
    active_reader_url = f"{browser_server['base_url']}/reader/{seed['active_comic_id']}"
    in_progress_reader_url = f"{browser_server['base_url']}/reader/{seed['in_progress_comic_id']}"

    page.goto(active_reader_url, wait_until="networkidle")

    page.wait_for_selector(".reader-container")
    page.locator(".nav-zone.center").click()
    page.locator("button[title='Settings']").click()
    page.locator("label:text('Double Page (d)')").locator("..").get_by_role("button").click()
    page.wait_for_timeout(250)

    page.wait_for_function(
        """
        () => {
            const reader = document.querySelector('.reader-container');
            const state = reader?._x_dataStack?.[0];
            return state?.viewMode === 'double'
                && !reader?.classList.contains('scroll-mode')
                && document.querySelectorAll('img.reader-page').length === 1;
        }
        """
    )

    page.reload(wait_until="networkidle")
    page.wait_for_function(
        """
        () => {
            const reader = document.querySelector('.reader-container');
            const state = reader?._x_dataStack?.[0];
            return state?.viewMode === 'double'
                && !reader?.classList.contains('scroll-mode')
                && document.querySelectorAll('img.reader-page').length === 1;
        }
        """
    )

    page.goto(in_progress_reader_url, wait_until="networkidle")
    page.wait_for_selector(".reader-container")
    page.wait_for_function(
        """
        () => {
            const reader = document.querySelector('.reader-container');
            const state = reader?._x_dataStack?.[0];
            return state?.viewMode === 'single'
                && !reader?.classList.contains('scroll-mode')
                && document.querySelectorAll('img.reader-page').length > 0;
        }
        """
    )


@pytest.mark.browser
def test_reader_manga_mode_inverts_zone_navigation(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/reader/{seed['active_comic_id']}", wait_until="networkidle")

    page.wait_for_selector(".reader-container")
    page.locator(".nav-zone.center").click()
    page.locator("button[title='Settings']").click()
    page.locator("label:text('Manga Mode (m)')").locator("..").get_by_role("button").click()
    page.wait_for_timeout(250)

    page.wait_for_function(
        """
        () => {
            const reader = document.querySelector('.reader-container');
            return reader?._x_dataStack?.[0]?.readDirection === 'rtl';
        }
        """
    )

    page.locator(".nav-zone.left").click()
    page.wait_for_timeout(300)
    current_page_text = page.locator(".reader-controls .text-white.font-bold").first.text_content()
    assert current_page_text == "2"

    page.locator(".nav-zone.right").click()
    page.wait_for_timeout(300)
    current_page_text = page.locator(".reader-controls .text-white.font-bold").first.text_content()
    assert current_page_text == "1"


@pytest.mark.browser
def test_reader_manga_mode_persists_per_comic_without_leaking_to_other_books(page, browser_server):
    seed = browser_server["seed"]
    active_reader_url = f"{browser_server['base_url']}/reader/{seed['active_comic_id']}"
    in_progress_reader_url = f"{browser_server['base_url']}/reader/{seed['in_progress_comic_id']}"

    page.goto(active_reader_url, wait_until="networkidle")

    page.wait_for_selector(".reader-container")
    page.locator(".nav-zone.center").click()
    page.locator("button[title='Settings']").click()
    page.locator("label:text('Manga Mode (m)')").locator("..").get_by_role("button").click()
    page.wait_for_timeout(250)

    page.wait_for_function(
        """
        () => {
            const reader = document.querySelector('.reader-container');
            const state = reader?._x_dataStack?.[0];
            return state?.readDirection === 'rtl' && !reader?.classList.contains('scroll-mode');
        }
        """
    )

    page.reload(wait_until="networkidle")
    page.wait_for_function(
        """
        () => {
            const reader = document.querySelector('.reader-container');
            const state = reader?._x_dataStack?.[0];
            return state?.readDirection === 'rtl'
                && !reader?.classList.contains('scroll-mode')
                && document.querySelectorAll('img.reader-page').length > 0;
        }
        """
    )

    page.goto(in_progress_reader_url, wait_until="networkidle")
    page.wait_for_selector(".reader-container")
    page.wait_for_function(
        """
        () => {
            const reader = document.querySelector('.reader-container');
            const state = reader?._x_dataStack?.[0];
            return state?.readDirection === 'ltr'
                && !reader?.classList.contains('scroll-mode')
                && document.querySelectorAll('img.reader-page').length > 0;
        }
        """
    )


@pytest.mark.browser
def test_reader_goto_modal_jumps_to_requested_page(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/reader/{seed['active_comic_id']}", wait_until="networkidle")

    page.wait_for_selector(".reader-container")
    page.keyboard.press("g")
    page.wait_for_selector("text=Go to Page")

    goto_input = page.locator("input[type='number']").first
    goto_input.fill("3")
    goto_input.press("Enter")
    page.wait_for_timeout(400)

    current_page_text = page.locator(".reader-controls .text-white.font-bold").first.text_content()
    assert current_page_text == "3"
    assert page.locator("text=Go to Page").first.is_visible() is False

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
def test_reader_mobile_swipe_left_advances_page(mobile_page, browser_server):
    seed = browser_server["seed"]
    mobile_page.goto(f"{browser_server['base_url']}/reader/{seed['active_comic_id']}", wait_until="networkidle")

    mobile_page.wait_for_selector(".reader-container")
    mobile_page.locator(".reader-container").evaluate(
        """
        (element) => {
            const touchStart = new TouchEvent('touchstart', {
                bubbles: true,
                cancelable: true,
                changedTouches: [new Touch({
                    identifier: 1,
                    target: element,
                    screenX: 320,
                    screenY: 420,
                    clientX: 320,
                    clientY: 420,
                    pageX: 320,
                    pageY: 420,
                })],
            });
            const touchEnd = new TouchEvent('touchend', {
                bubbles: true,
                cancelable: true,
                changedTouches: [new Touch({
                    identifier: 1,
                    target: element,
                    screenX: 120,
                    screenY: 420,
                    clientX: 120,
                    clientY: 420,
                    pageX: 120,
                    pageY: 420,
                })],
            });
            element.dispatchEvent(touchStart);
            element.dispatchEvent(touchEnd);
        }
        """
    )
    mobile_page.wait_for_timeout(350)

    current_page_text = mobile_page.locator(".reader-controls .text-white.font-bold").first.text_content()
    assert current_page_text == "2"


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
def test_reader_long_view_mode_persists_per_comic_without_leaking_to_other_books(page, browser_server):
    seed = browser_server["seed"]
    active_reader_url = f"{browser_server['base_url']}/reader/{seed['active_comic_id']}"
    in_progress_reader_url = f"{browser_server['base_url']}/reader/{seed['in_progress_comic_id']}"

    page.goto(active_reader_url, wait_until="networkidle")

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

    page.goto(in_progress_reader_url, wait_until="networkidle")
    page.wait_for_selector(".reader-container")
    page.wait_for_function(
        """
        () => {
            const reader = document.querySelector('.reader-container');
            const state = reader?._x_dataStack?.[0];
            return !reader?.classList.contains('scroll-mode')
                && state?.readingMode === 'paged'
                && document.querySelectorAll('img.reader-page').length > 0;
        }
        """
    )


@pytest.mark.browser
def test_reader_incognito_mode_does_not_persist_per_book_overrides(page, browser_server):
    seed = browser_server["seed"]
    active_reader_url = f"{browser_server['base_url']}/reader/{seed['active_comic_id']}"
    incognito_reader_url = f"{active_reader_url}?incognito=true"

    page.goto(incognito_reader_url, wait_until="networkidle")

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
                && state?.isIncognito === true
                && document.querySelectorAll('[data-scroll-page-index]').length > 0;
        }
        """
    )

    stored_overrides = page.evaluate("() => window.localStorage.getItem('reader_comicOverrides')")
    assert stored_overrides is None

    page.goto(active_reader_url, wait_until="networkidle")
    page.wait_for_selector(".reader-container")
    page.wait_for_function(
        """
        () => {
            const reader = document.querySelector('.reader-container');
            const state = reader?._x_dataStack?.[0];
            return !reader?.classList.contains('scroll-mode')
                && state?.readingMode === 'paged'
                && document.querySelectorAll('img.reader-page').length > 0;
        }
        """
    )


@pytest.mark.browser
def test_reader_long_view_navigation_arrows_snap_between_archive_pages(page, browser_server, monkeypatch):
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

    next_page_button = page.locator(".reader-controls button").nth(2)
    next_page_button.click()
    page.wait_for_timeout(750)
    debug_after_click = page.evaluate(
        """
        () => {
            const reader = document.querySelector('.reader-container');
            const state = reader?._x_dataStack?.[0];
            const target = document.querySelector('[data-scroll-page-index="1"]');
            return {
                currentPage: state?.currentPage,
                scrollTop: reader?.scrollTop ?? null,
                targetTop: target?.getBoundingClientRect().top ?? null,
            };
        }
        """
    )
    assert debug_after_click["currentPage"] == 1, debug_after_click
    assert debug_after_click["scrollTop"] > 500, debug_after_click
    assert debug_after_click["targetTop"] < 140, debug_after_click

    prev_page_button = page.locator(".reader-controls button").nth(1)
    prev_page_button.click()
    page.wait_for_timeout(750)
    debug_after_prev_click = page.evaluate(
        """
        () => {
            const reader = document.querySelector('.reader-container');
            const state = reader?._x_dataStack?.[0];
            const target = document.querySelector('[data-scroll-page-index="0"]');
            return {
                currentPage: state?.currentPage,
                scrollTop: reader?.scrollTop ?? null,
                targetTop: target?.getBoundingClientRect().top ?? null,
            };
        }
        """
    )
    assert debug_after_prev_click["currentPage"] == 0, debug_after_prev_click
    assert debug_after_prev_click["scrollTop"] < 50, debug_after_prev_click
    assert debug_after_prev_click["targetTop"] < 140, debug_after_prev_click


@pytest.mark.browser
def test_reader_long_view_scrubber_moves_to_selected_archive_page(page, browser_server, monkeypatch):
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

    scrubber = page.locator("input.scrubber")
    scrubber_box = scrubber.bounding_box()
    assert scrubber_box is not None
    scrubber.click(position={"x": scrubber_box["width"] * 0.92, "y": scrubber_box["height"] / 2})

    page.wait_for_timeout(750)
    debug_after_scrub = page.evaluate(
        """
        () => {
            const reader = document.querySelector('.reader-container');
            const state = reader?._x_dataStack?.[0];
            const target = document.querySelector('[data-scroll-page-index="2"]');
            return {
                currentPage: state?.currentPage,
                scrollTop: reader?.scrollTop ?? null,
                targetTop: target?.getBoundingClientRect().top ?? null,
                scrubberValue: state?.scrubberValue ?? null,
            };
        }
        """
    )
    assert debug_after_scrub["currentPage"] == 2, debug_after_scrub
    assert debug_after_scrub["scrollTop"] > 5000, debug_after_scrub
    assert debug_after_scrub["targetTop"] < 140, debug_after_scrub
    assert int(debug_after_scrub["scrubberValue"]) == 2, debug_after_scrub


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
