import pytest


@pytest.mark.browser
def test_continue_reading_page_shows_in_progress_items_and_opens_reader(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/continue-reading", wait_until="networkidle")

    page.get_by_role("heading", name="Continue Reading").wait_for()
    page.wait_for_selector(f"text={seed['in_progress_comic_title']}")

    assert page.locator(f"text={seed['completed_comic_title']}").count() == 0

    page.get_by_role("link", name="Continue").click()

    page.wait_for_url(f"**/reader/{seed['in_progress_comic_id']}")
    page.locator(".reader-container").wait_for()
    page.locator(".nav-zone.center").click()
    assert page.locator(".reader-toolbar").filter(has_text=seed["in_progress_comic_title"]).first.is_visible()


@pytest.mark.browser
def test_reading_list_can_open_reader_and_return(page, browser_server):
    seed = browser_server["seed"]
    page.goto(
        f"{browser_server['base_url']}/reading-lists/{seed['reading_list_id']}",
        wait_until="networkidle",
    )

    page.get_by_role("heading", name=seed["reading_list_name"]).wait_for()
    page.get_by_role("button", name="Start Reading").click()

    page.wait_for_url(f"**/reader/{seed['active_comic_id']}*")
    page.locator(".reader-container").wait_for()

    page.locator(".nav-zone.center").click()
    context_badge = page.locator(".reader-toolbar").filter(has_text=seed["reading_list_name"]).first
    context_badge.wait_for()

    page.get_by_role("button", name="Close").click()

    page.wait_for_url(f"**/reading-lists/{seed['reading_list_id']}")
    page.get_by_role("heading", name=seed["reading_list_name"]).wait_for()


@pytest.mark.browser
def test_cover_browser_switches_view_modes_and_returns_to_reading_list(page, browser_server):
    seed = browser_server["seed"]
    page.goto(
        f"{browser_server['base_url']}/reading-lists/{seed['reading_list_id']}",
        wait_until="networkidle",
    )

    page.get_by_role("heading", name=seed["reading_list_name"]).wait_for()
    page.locator("a[title='Cover Browser']").click()

    page.wait_for_url(f"**/browse/reading_list/{seed['reading_list_id']}")
    page.locator(".browser-container").wait_for()
    page.locator(f"text=1 / 3").wait_for()

    page.get_by_role("button", name="Theater View (t)").click()
    theater_label = page.locator(".theater-view .absolute.bottom-8 span")
    theater_label.wait_for()
    assert f"Smoke Series #{seed['active_comic_number']}" in theater_label.inner_text()

    page.keyboard.press("ArrowRight")
    page.locator("text=2 / 3").wait_for()

    page.get_by_role("button", name="Close").click()

    page.wait_for_url(f"**/reading-lists/{seed['reading_list_id']}")
    page.get_by_role("heading", name=seed["reading_list_name"]).wait_for()


@pytest.mark.browser
def test_quick_search_navigates_to_reading_list(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")

    search_input = page.locator("nav input[type='search']").first
    search_input.fill("Crossover")

    page.locator("text=Reading Lists").first.wait_for()
    result_link = page.get_by_role("link", name=seed["reading_list_name"]).first
    result_link.wait_for()
    result_link.click()

    page.wait_for_url(f"**/reading-lists/{seed['reading_list_id']}")
    page.get_by_role("heading", name=seed["reading_list_name"]).wait_for()
