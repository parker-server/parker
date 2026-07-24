import pytest
from datetime import timedelta
from sqlalchemy import select

from app.models.comic import Volume
from app.models.interactions import UserVolumeFollow
from tests.factories import create_comic


def _insert_following_arrival(browser_server):
    seed = browser_server["seed"]
    session = browser_server["db_factory"]()
    try:
        follow = session.scalar(
            select(UserVolumeFollow).where(
                UserVolumeFollow.user_id == seed["user_id"],
                UserVolumeFollow.volume_id == seed["volume_id"],
            )
        )
        assert follow is not None

        volume = session.get(Volume, seed["volume_id"])
        root = volume.series.library.active_root
        new_issue = create_comic(
            session, volume, root, "smoke-future-shock.cbz",
            number="4",
            title="Smoke Future Shock",
            year=2026,
            page_count=3,
            created_at=follow.followed_at + timedelta(minutes=1),
            updated_at=follow.followed_at + timedelta(minutes=1),
            filename="smoke-future-shock.cbz",
        )
        session.commit()
        session.refresh(new_issue)
        return new_issue.id, new_issue.title
    finally:
        session.close()


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


@pytest.mark.browser
def test_volume_detail_follow_toggle_persists_after_reload(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/volumes/{seed['volume_id']}", wait_until="networkidle")

    page.get_by_role("heading", name="Volume 1").wait_for()
    follow_button = page.get_by_role("button", name="Follow")
    follow_button.click()

    page.get_by_role("button", name="Following").wait_for()

    page.reload(wait_until="networkidle")
    page.get_by_role("button", name="Following").wait_for()


@pytest.mark.browser
def test_dashboard_following_page_can_unfollow_volume(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/volumes/{seed['volume_id']}", wait_until="networkidle")

    page.get_by_role("button", name="Follow").click()
    page.get_by_role("button", name="Following").wait_for()

    page.goto(f"{browser_server['base_url']}/user/dashboard", wait_until="networkidle")
    page.get_by_role("link", name="Manage Following").click()

    page.wait_for_url("**/user/following")
    page.get_by_role("heading", name="Following").wait_for()
    page.wait_for_selector(f"text={seed['series_name']}")

    page.get_by_role("button", name="Unfollow").click()

    page.wait_for_selector("text=Nothing followed yet")
    assert page.locator(f"text={seed['series_name']}").count() == 0


@pytest.mark.browser
def test_pinned_library_home_rail_opens_series_detail(page, browser_server):
    seed = browser_server["seed"]

    page.goto(f"{browser_server['base_url']}/libraries", wait_until="networkidle")
    page.get_by_role("heading", name="Your Libraries").wait_for()
    page.get_by_role("button", name="Pin Browser Test Library to Home").click()
    page.get_by_role("button", name="Unpin Browser Test Library from Home").wait_for()

    page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")
    page.get_by_role("heading", name="Pinned Libraries").wait_for()
    pinned_block = page.locator("div").filter(has=page.get_by_role("heading", name="Pinned Libraries")).first
    pinned_block.get_by_role("heading", name="Browser Test Library").wait_for()
    pinned_block.locator(f'a[href*="/series/{seed["series_id"]}"]').first.click()

    page.wait_for_url(f"**/series/{seed['series_id']}")
    page.get_by_role("heading", name=seed["series_name"]).wait_for()


@pytest.mark.browser
def test_home_following_arrival_appears_after_import_and_clears_after_reading(page, browser_server):
    seed = browser_server["seed"]

    page.goto(f"{browser_server['base_url']}/volumes/{seed['volume_id']}", wait_until="networkidle")
    page.get_by_role("button", name="Follow").click()
    page.get_by_role("button", name="Following").wait_for()

    page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")
    assert page.get_by_role("heading", name="New from Following").count() == 0

    new_issue_id, new_issue_title = _insert_following_arrival(browser_server)

    page.reload(wait_until="networkidle")
    rail = page.locator("div").filter(has=page.get_by_role("heading", name="New from Following")).first
    page.get_by_role("heading", name="New from Following").wait_for()
    rail.locator(f"text={new_issue_title}").first.wait_for()

    rail.locator(f'a[href*="/reader/{new_issue_id}"]').first.click()
    page.wait_for_url(f"**/reader/{new_issue_id}*")
    page.locator(".reader-container").wait_for()
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(300)

    page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")
    assert page.get_by_role("heading", name="New from Following").count() == 0

    resume_rail = page.locator("div").filter(has=page.get_by_role("heading", name="Jump Back In")).first
    page.get_by_role("heading", name="Jump Back In").wait_for()
    resume_rail.locator(f"text={new_issue_title}").first.wait_for()
