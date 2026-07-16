import pytest
from sqlalchemy import select

from app.models.reading_progress import ReadingProgress


def _get_progress(db_factory, user_id, comic_id):
    session = db_factory()
    try:
        return session.scalar(
            select(ReadingProgress).where(
                ReadingProgress.user_id == user_id,
                ReadingProgress.comic_id == comic_id,
            )
        )
    finally:
        session.close()


@pytest.mark.browser
def test_reader_keyboard_issue_navigation_moves_between_comics(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/reader/{seed['active_comic_id']}", wait_until="networkidle")

    page.locator(".reader-container").wait_for()

    page.keyboard.press("]")
    page.wait_for_url(f"**/reader/{seed['in_progress_comic_id']}")
    page.locator("img.reader-page").wait_for()

    page.locator(".nav-zone.center").click()
    toolbar = page.locator(".reader-toolbar")
    toolbar.filter(has_text=seed["in_progress_comic_title"]).first.wait_for()

    page.keyboard.press("[")
    page.wait_for_url(f"**/reader/{seed['active_comic_id']}")
    page.locator(".nav-zone.center").click()
    toolbar.filter(has_text=seed["active_comic_title"]).first.wait_for()


@pytest.mark.browser
def test_reader_escape_exits_to_comic_detail_without_context(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/reader/{seed['active_comic_id']}", wait_until="networkidle")

    page.locator(".reader-container").wait_for()
    page.keyboard.press("Escape")

    page.wait_for_url(f"**/comics/{seed['active_comic_id']}")
    page.get_by_role("heading", name=f"{seed['series_name']} #{seed['active_comic_number']}").wait_for()


@pytest.mark.browser
def test_reader_incognito_does_not_persist_progress(page, browser_server):
    seed = browser_server["seed"]
    page.goto(
        f"{browser_server['base_url']}/reader/{seed['active_comic_id']}?incognito=true",
        wait_until="networkidle",
    )

    page.locator(".reader-container").wait_for()
    page.keyboard.press("ArrowRight")
    page.wait_for_function(
        "document.querySelector('.reader-container')._x_dataStack[0].currentPage === 1"
    )

    progress = _get_progress(browser_server["db_factory"], seed["user_id"], seed["active_comic_id"])
    assert progress is None


@pytest.mark.browser
def test_quick_search_person_result_opens_filtered_search(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")

    search_input = page.locator("nav input[type='search']").first
    search_input.fill("Casey")

    page.locator("text=People").first.wait_for()
    page.get_by_role("link", name="Casey Smoke").first.click()

    page.wait_for_url("**/search?*")
    assert "filters=" in page.url

    results_section = page.locator("div[x-show='hasSearched']")
    results_section.wait_for()
    page.wait_for_selector("text=Results")
    result_title = results_section.locator("p.text-sm.text-gray-400").filter(
        has_text=seed["in_progress_comic_title"]
    ).first
    result_title.wait_for()
    assert result_title.is_visible()


@pytest.mark.browser
def test_search_page_hydrates_state_key_payload(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")

    target_url = page.evaluate(
        """
        () => {
            const filters = [
                { field: 'writer', operator: 'equal', value: ['Casey Smoke'] }
            ];

            for (let i = 0; i < 60; i += 1) {
                filters.push({
                    field: 'series',
                    operator: 'contains',
                    value: [`Long filler search handoff value ${i} with enough text to exceed the URL limit`]
                });
            }

            return new URL(
                window.parker.searchHandoff.buildUrl({ match: 'any', filters }),
                window.location.origin
            ).toString();
        }
        """
    )

    page.goto(target_url, wait_until="networkidle")
    assert "state_key=" in page.url

    results_section = page.locator("div[x-show='hasSearched']")
    results_section.wait_for()
    page.wait_for_selector("text=Results")
    result_title = results_section.locator("p.text-sm.text-gray-400").filter(
        has_text=seed["in_progress_comic_title"]
    ).first
    result_title.wait_for()
    assert result_title.is_visible()
