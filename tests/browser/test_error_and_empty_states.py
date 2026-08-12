import pytest
from sqlalchemy import delete

from app.models.reading_progress import ReadingProgress


def _clear_progress(db_factory):
    session = db_factory()
    try:
        session.execute(delete(ReadingProgress))
        session.commit()
    finally:
        session.close()


@pytest.mark.browser
def test_continue_reading_empty_states_show_expected_messages(page, browser_server):
    _clear_progress(browser_server["db_factory"])

    page.goto(f"{browser_server['base_url']}/continue-reading", wait_until="networkidle")

    page.get_by_role("heading", name="Continue Reading").wait_for()
    page.wait_for_selector("text=No comics currently in progress.")
    page.get_by_role("link", name="Browse Library").wait_for()

    page.get_by_role("button", name="Recently Read").click()
    page.wait_for_selector("text=No reading history found.")

    page.get_by_role("button", name="Completed").click()
    page.wait_for_selector("text=No completed comics yet.")


@pytest.mark.browser
def test_reading_list_not_found_shows_error_state(page, browser_server):
    page.goto(f"{browser_server['base_url']}/reading-lists/99999", wait_until="networkidle")

    page.get_by_role("heading", name="Reading List Not Found").wait_for()
    page.wait_for_selector("text=The reading list you are looking for does not exist or may have been deleted.")
    page.get_by_role("button", name="Go Back").wait_for()


@pytest.mark.browser
def test_search_page_shows_no_results_state(page, browser_server):
    page.goto(f"{browser_server['base_url']}/search", wait_until="networkidle")

    page.get_by_role("heading", name="Advanced Search").wait_for()

    first_rule = page.locator(".space-y-4 > div").first
    first_rule.locator("select").nth(0).select_option("title")
    rule_input = first_rule.locator("input[x-model='rule.value'][type='text']")
    rule_input.fill("No Such Browser Test Comic")

    page.get_by_role("button", name="Search Comics").click()

    results_section = page.locator("div[x-show='hasSearched']")
    results_section.wait_for()
    page.wait_for_selector("text=Results")
    page.wait_for_selector("text=No comics found matching your criteria.")
