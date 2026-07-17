import pytest


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
    mobile_page.get_by_role("button", name="Start Reading").wait_for()
    mobile_page.get_by_role("button", name="Start Reading").click()

    mobile_page.wait_for_url(f"**/reader/{seed['active_comic_id']}*")
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
