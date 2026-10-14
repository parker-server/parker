import pytest

from app.services.settings_service import SettingsService


@pytest.mark.browser
def test_mobile_menu_exposes_navigation_and_quick_search(mobile_page, browser_server):
    seed = browser_server["seed"]
    mobile_page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")

    mobile_page.locator("nav button.lg\\:hidden").click()
    mobile_page.get_by_role("link", name="Reading Lists").wait_for()

    search_input = mobile_page.locator("nav input[type='search']").nth(1)
    search_input.fill("Crossover")

    result_link = mobile_page.get_by_role("link", name=seed["reading_list_name"]).first
    result_link.wait_for()
    result_link.click()

    mobile_page.wait_for_url(f"**/reading-lists/{seed['reading_list_id']}")
    mobile_page.get_by_role("heading", name=seed["reading_list_name"]).wait_for()


@pytest.mark.browser
def test_mobile_reading_list_can_start_reader_flow(mobile_page, browser_server):
    seed = browser_server["seed"]
    mobile_page.goto(
        f"{browser_server['base_url']}/reading-lists/{seed['reading_list_id']}",
        wait_until="networkidle",
    )

    mobile_page.get_by_role("heading", name=seed["reading_list_name"]).wait_for()
    mobile_page.get_by_role("button", name="Continue").wait_for()
    mobile_page.get_by_role("button", name="Continue").click()

    mobile_page.wait_for_url(f"**/reader/{seed['in_progress_comic_id']}*")
    mobile_page.locator(".reader-container").wait_for()
    mobile_page.locator(".nav-zone.center").click()
    mobile_page.get_by_role("button", name="Close").wait_for()


@pytest.mark.browser
def test_mobile_reader_tap_navigation_advances_page(mobile_page, browser_server):
    mobile_page.goto(
        f"{browser_server['base_url']}/reader/{browser_server['seed']['active_comic_id']}",
        wait_until="networkidle",
    )

    mobile_page.locator(".reader-container").wait_for()
    mobile_page.locator(".nav-zone.right").click(position={"x": 10, "y": 200})

    mobile_page.wait_for_function(
        "document.querySelector('.reader-container')._x_dataStack[0].currentPage === 1"
    )
    mobile_page.locator(".nav-zone.center").click()
    mobile_page.get_by_role("button", name="Close").wait_for()
    assert mobile_page.locator("[data-ui-lock-toggle]").is_hidden()
    assert mobile_page.locator("[data-fullscreen-toggle]").is_hidden()
    assert mobile_page.locator("[data-keyboard-shortcuts-toggle]").is_hidden()
    assert mobile_page.locator("[data-magnifier-toggle]").is_hidden()
    assert mobile_page.locator("[data-settings-toggle]").is_hidden()
    assert mobile_page.locator("[data-bookmarks-toggle]").is_hidden()

    toolbar_menu_toggle = mobile_page.locator("[data-reader-toolbar-menu-toggle]")
    toolbar_menu_toggle.wait_for(state="visible")
    toolbar_menu_toggle.click()

    mobile_page.locator("[data-reader-toolbar-menu]").wait_for(state="visible")
    assert mobile_page.locator("[data-reader-toolbar-lock]").is_visible()
    assert mobile_page.locator("[data-reader-toolbar-settings]").is_visible()
    assert mobile_page.locator("[data-reader-toolbar-bookmarks]").is_visible()
    assert mobile_page.locator("[data-reader-toolbar-magnifier]").is_hidden()

    mobile_page.locator("[data-reader-toolbar-lock]").click()
    mobile_page.wait_for_function(
        "document.querySelector('.reader-container')._x_dataStack[0].uiLocked === true"
    )
    mobile_page.locator("[data-reader-toolbar-menu]").wait_for(state="hidden")


@pytest.mark.browser
def test_mobile_reader_uses_single_page_when_double_page_is_saved(mobile_page, browser_server):
    seed = browser_server["seed"]
    mobile_page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")
    mobile_page.evaluate("() => window.parker.storage.setString('reader_viewMode', 'double')")

    mobile_page.goto(f"{browser_server['base_url']}/reader/{seed['active_comic_id']}", wait_until="networkidle")
    mobile_page.locator(".reader-container").wait_for()
    mobile_page.wait_for_function(
        """
        () => {
            const state = document.querySelector('.reader-container')?._x_dataStack?.[0];
            return state?.viewMode === 'double'
                && state?.isCompactReaderLayout === true
                && state?.isDoublePageAvailable === false
                && state?.activeViewMode === 'single'
                && state?.pagesToDisplay?.length === 1
                && document.querySelectorAll('img.reader-page').length === 1;
        }
        """
    )

    mobile_page.locator(".nav-zone.center").click()
    mobile_page.locator("[data-reader-toolbar-menu-toggle]").click()
    mobile_page.locator("[data-reader-toolbar-settings]").click()

    mobile_page.locator(".settings-panel").wait_for(state="visible")
    assert mobile_page.locator("[data-double-page-setting]").is_hidden()

    mobile_page.keyboard.press("d")
    mobile_page.wait_for_function(
        """
        () => {
            const state = document.querySelector('.reader-container')?._x_dataStack?.[0];
            return state?.viewMode === 'double'
                && state?.activeViewMode === 'single'
                && state?.pagesToDisplay?.length === 1
                && document.querySelectorAll('img.reader-page').length === 1;
        }
        """
    )


@pytest.mark.browser
@pytest.mark.parametrize(
    "background_style",
    ["static_cover", "solid_color"],
)
def test_mobile_login_background_fills_the_viewport(
    mobile_page, browser_server, background_style
):
    session = browser_server["db_factory"]()
    try:
        service = SettingsService(session)
        service.initialize_defaults()
        service.update("ui.login_background_style", background_style)
    finally:
        session.close()

    mobile_page.goto(f"{browser_server['base_url']}/login", wait_until="networkidle")

    background = mobile_page.locator("[data-login-background]")
    assert background.evaluate("element => getComputedStyle(element).position") == "fixed"

    bounds = background.bounding_box()
    viewport = mobile_page.evaluate("() => ({ width: innerWidth, height: innerHeight })")
    assert bounds == {
        "x": 0,
        "y": 0,
        "width": viewport["width"],
        "height": viewport["height"],
    }

    overlays = mobile_page.locator("[data-login-overlay]")
    assert overlays.count() == 2
    for index in range(overlays.count()):
        overlay = overlays.nth(index)
        assert overlay.evaluate("element => getComputedStyle(element).position") == "fixed"
        assert overlay.bounding_box() == bounds

    page_size = mobile_page.evaluate(
        """
        () => ({
            viewportWidth: document.documentElement.clientWidth,
            viewportHeight: document.documentElement.clientHeight,
            pageWidth: document.documentElement.scrollWidth,
            pageHeight: document.documentElement.scrollHeight,
            rootOverflow: getComputedStyle(document.documentElement).overflow,
        })
        """
    )
    assert page_size["pageWidth"] == page_size["viewportWidth"], page_size
    assert page_size["pageHeight"] == page_size["viewportHeight"], page_size
    assert page_size["rootOverflow"] == "hidden"


@pytest.mark.browser
def test_mobile_login_cover_information_is_centered(mobile_page, browser_server):
    session = browser_server["db_factory"]()
    try:
        service = SettingsService(session)
        service.initialize_defaults()
        service.update("ui.login_background_style", "static_cover")
    finally:
        session.close()

    mobile_page.goto(f"{browser_server['base_url']}/login", wait_until="networkidle")
    cover_info_button = mobile_page.get_by_role("button", name="About this cover")
    assert cover_info_button.evaluate(
        """
        (button) => {
            const bounds = button.getBoundingClientRect();
            const target = document.elementFromPoint(
                bounds.left + (bounds.width / 2),
                bounds.top + (bounds.height / 2),
            );
            return target?.closest('button') === button;
        }
        """
    )
    cover_info_button.click()

    dialog = mobile_page.get_by_role("dialog")
    dialog.wait_for(state="visible")
    bounds = dialog.bounding_box()
    viewport = mobile_page.evaluate("() => ({ width: innerWidth, height: innerHeight })")

    assert abs(bounds["x"] + (bounds["width"] / 2) - (viewport["width"] / 2)) < 1
    assert abs(bounds["y"] + (bounds["height"] / 2) - (viewport["height"] / 2)) < 1


@pytest.mark.browser
def test_tablet_login_page_does_not_overflow(page, browser_server):
    page.set_viewport_size({"width": 800, "height": 1280})
    page.goto(f"{browser_server['base_url']}/login", wait_until="networkidle")

    page_size = page.evaluate(
        """
        () => ({
            viewportWidth: document.documentElement.clientWidth,
            viewportHeight: document.documentElement.clientHeight,
            pageWidth: document.documentElement.scrollWidth,
            pageHeight: document.documentElement.scrollHeight,
            rootOverflow: getComputedStyle(document.documentElement).overflow,
        })
        """
    )

    assert page_size["pageWidth"] == page_size["viewportWidth"], page_size
    assert page_size["pageHeight"] == page_size["viewportHeight"], page_size
    assert page_size["rootOverflow"] == "hidden"
