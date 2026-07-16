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
def test_continue_reading_tabs_show_expected_items(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/continue-reading", wait_until="networkidle")

    page.get_by_role("heading", name="Continue Reading").wait_for()
    page.wait_for_selector(f"text={seed['in_progress_comic_title']}")
    assert page.locator(f"text={seed['completed_comic_title']}").count() == 0

    page.get_by_role("button", name="Completed").click()
    page.wait_for_selector(f"text={seed['completed_comic_title']}")
    assert page.locator(f"text={seed['in_progress_comic_title']}").count() == 0

    page.get_by_role("button", name="Recently Read").click()
    page.wait_for_selector(f"text={seed['completed_comic_title']}")
    page.wait_for_selector(f"text={seed['in_progress_comic_title']}")


@pytest.mark.browser
def test_continue_reading_mark_read_moves_issue_to_completed(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/continue-reading", wait_until="networkidle")

    page.get_by_role("heading", name="Continue Reading").wait_for()
    card = page.locator("div.border.border-gray-700").filter(has_text=seed["in_progress_comic_title"]).first
    card.get_by_role("button", name="Mark Read").click()

    page.wait_for_timeout(300)
    progress = _get_progress(browser_server["db_factory"], seed["user_id"], seed["in_progress_comic_id"])
    assert progress is not None
    assert progress.completed is True

    page.get_by_role("button", name="Completed").click()
    page.wait_for_selector(f"text={seed['in_progress_comic_title']}")


@pytest.mark.browser
def test_continue_reading_remove_clears_progress_after_confirmation(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/continue-reading", wait_until="networkidle")

    page.get_by_role("heading", name="Continue Reading").wait_for()
    card = page.locator("div.border.border-gray-700").filter(has_text=seed["in_progress_comic_title"]).first
    card.get_by_role("button", name="Remove").click()

    page.get_by_role("heading", name="Clear progress?").wait_for()
    page.get_by_role("button", name="Clear").click()

    page.wait_for_timeout(300)
    progress = _get_progress(browser_server["db_factory"], seed["user_id"], seed["in_progress_comic_id"])
    assert progress is None
    assert page.locator(f"text={seed['in_progress_comic_title']}").count() == 0
