import pytest


@pytest.mark.browser
def test_search_page_finds_matching_comic_by_title(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/search", wait_until="networkidle")

    page.get_by_role("heading", name="Advanced Search").wait_for()

    first_rule = page.locator(".space-y-4 > div").first
    first_rule.locator("select").nth(0).select_option("title")
    rule_input = first_rule.locator("input[x-model='rule.value'][type='text']")
    rule_input.fill(seed["active_comic_title"])

    page.get_by_role("button", name="Search Comics").click()

    results_section = page.locator("div[x-show='hasSearched']")
    results_section.wait_for()
    page.wait_for_selector("text=Results")
    results_title = results_section.locator("p.text-sm.text-gray-400").filter(has_text=seed["active_comic_title"]).first
    results_title.wait_for()
    assert results_title.is_visible()


@pytest.mark.browser
def test_series_detail_page_filters_read_items(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/series/{seed['series_id']}", wait_until="networkidle")

    page.wait_for_selector(f"text={seed['series_name']}")
    page.wait_for_selector(f"text={seed['completed_comic_title']}")
    page.wait_for_selector(f"text={seed['active_comic_title']}")

    page.get_by_role("button", name="Read Only", exact=True).click()

    page.wait_for_timeout(300)
    assert page.locator(f"text={seed['completed_comic_title']}").first.is_visible()
    assert page.locator(f"text={seed['active_comic_title']}").count() == 0
