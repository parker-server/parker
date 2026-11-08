import pytest

from datetime import date, datetime, timedelta, timezone

from app.models.comic import Comic
from app.models.promoted_rail import PromotedRail
from app.models.reading_progress import ReadingProgress


def _visible_home_rail_keys(page):
    return page.locator("[data-home-rail]").evaluate_all(
        """
        (elements) => elements
            .filter((element) => getComputedStyle(element).display !== 'none')
            .map((element) => element.dataset.homeRail)
        """
    )


def _wait_for_visible_rail_order(page, expected_prefix):
    page.wait_for_function(
        """
        (expectedPrefix) => {
            const visibleKeys = [...document.querySelectorAll('[data-home-rail]')]
                .filter((element) => getComputedStyle(element).display !== 'none')
                .map((element) => element.dataset.homeRail);
            return expectedPrefix.every((key, index) => visibleKeys[index] === key);
        }
        """,
        arg=expected_prefix,
    )


@pytest.mark.browser
def test_home_customize_order_persists_after_reload(page, browser_server):
    page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")
    page.get_by_role("heading", name="Jump Back In").wait_for()
    page.get_by_role("heading", name="Up Next").wait_for()

    page.get_by_role("button", name="Customize Home").click()
    dialog = page.get_by_role("dialog", name="Customize Home")

    for _ in range(2):
        dialog.locator('[data-home-rail-draft="up_next"]').locator("button[title='Move up']").click()

    with page.expect_response(
        lambda response: "/api/home/layout" in response.url and response.request.method == "PUT"
    ):
        dialog.get_by_role("button", name="Save Home Layout").click()

    _wait_for_visible_rail_order(page, ["up_next", "resume"])

    page.reload(wait_until="networkidle")
    page.get_by_role("heading", name="Up Next").wait_for()
    _wait_for_visible_rail_order(page, ["up_next", "resume"])


@pytest.mark.browser
def test_home_hidden_rail_is_absent_and_not_fetched_after_reload(page, browser_server):
    request_urls = []
    page.on("request", lambda request: request_urls.append(request.url))

    page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")
    page.get_by_role("heading", name="Jump Back In").wait_for()

    page.get_by_role("button", name="Customize Home").click()
    dialog = page.get_by_role("dialog", name="Customize Home")
    dialog.locator('[data-home-rail-draft="resume"] label').click()
    assert not dialog.get_by_label("Show Jump Back In").is_checked()

    with page.expect_response(
        lambda response: "/api/home/layout" in response.url and response.request.method == "PUT"
    ):
        dialog.get_by_role("button", name="Save Home Layout").click()

    assert not page.get_by_role("heading", name="Jump Back In").is_visible()

    request_urls.clear()
    page.reload(wait_until="networkidle")
    page.get_by_role("heading", name="Up Next").wait_for()

    assert not page.get_by_role("heading", name="Jump Back In").is_visible()
    assert "resume" not in _visible_home_rail_keys(page)
    assert not any("/api/home/resume" in url for url in request_urls)


@pytest.mark.browser
def test_home_hide_all_rails_fallback_can_reset(page, browser_server):
    page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")
    page.get_by_role("heading", name="Jump Back In").wait_for()

    page.get_by_role("button", name="Customize Home").click()
    dialog = page.get_by_role("dialog", name="Customize Home")
    dialog.get_by_role("button", name="Hide All").click()

    with page.expect_response(
        lambda response: "/api/home/layout" in response.url and response.request.method == "PUT"
    ):
        dialog.get_by_role("button", name="Save Home Layout").click()

    page.get_by_role("heading", name="No Home Rails Are Visible").wait_for()
    page.get_by_role("button", name="Customize Home").wait_for()

    with page.expect_response(
        lambda response: "/api/home/layout/reset" in response.url and response.request.method == "POST"
    ):
        page.get_by_role("button", name="Reset Home Layout").click()

    page.get_by_role("heading", name="Jump Back In").wait_for()
    assert _visible_home_rail_keys(page)[0] == "resume"


@pytest.mark.browser
def test_home_can_hide_a_featured_rail_without_fetching_it_again(
        page,
        browser_server,
        browser_db_factory,
):
    session = browser_db_factory()
    try:
        rail = PromotedRail(
            title="Browser Halloween",
            query_config={
                "match": "all",
                "filters": [{"field": "title", "operator": "contains", "value": "Smoke"}],
                "sort_by": "title",
                "sort_order": "asc",
            },
            starts_on=date.today() - timedelta(days=1),
            ends_on=date.today() + timedelta(days=1),
            enabled=True,
            display_order=0,
        )
        session.add(rail)
        session.commit()
        session.refresh(rail)
        rail_id = rail.id
    finally:
        session.close()

    request_urls = []
    page.on("request", lambda request: request_urls.append(request.url))
    page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")
    page.get_by_role("heading", name="Browser Halloween").wait_for()

    page.get_by_role("button", name="Customize Home").click()
    dialog = page.get_by_role("dialog", name="Customize Home")
    featured_draft = dialog.locator(f'[data-home-rail-draft="promoted:{rail_id}"]')
    assert featured_draft.get_by_label("Show Browser Halloween").is_checked()
    assert featured_draft.locator("button[title*='administrator order']").is_disabled()
    featured_draft.locator("label").click()

    with page.expect_response(
        lambda response: "/api/home/layout" in response.url and response.request.method == "PUT"
    ):
        dialog.get_by_role("button", name="Save Home Layout").click()

    assert not page.get_by_role("heading", name="Browser Halloween").is_visible()

    request_urls.clear()
    page.reload(wait_until="networkidle")
    page.get_by_role("heading", name="Jump Back In").wait_for()
    assert not page.get_by_role("heading", name="Browser Halloween").is_visible()
    assert not any(f"/api/home/promoted/{rail_id}/items" in url for url in request_urls)


@pytest.mark.browser
def test_featured_rail_view_all_hands_its_query_to_advanced_search(
        page,
        browser_server,
        browser_db_factory,
):
    seed = browser_server["seed"]
    session = browser_db_factory()
    try:
        rail = PromotedRail(
            title="Search Handoff Rail",
            query_config={
                "match": "all",
                "filters": [{"field": "title", "operator": "contains", "value": "Smoke"}],
                "sort_by": "title",
                "sort_order": "asc",
            },
            starts_on=date.today() - timedelta(days=1),
            ends_on=date.today() + timedelta(days=1),
            enabled=True,
            display_order=0,
        )
        session.add(rail)
        session.commit()
        session.refresh(rail)
        rail_id = rail.id
    finally:
        session.close()

    page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")
    featured_section = page.locator(f'[data-home-rail="promoted:{rail_id}"]')
    featured_section.get_by_role("heading", name="Search Handoff Rail").wait_for()
    series_link = featured_section.locator(f'a[href="/series/{seed["series_id"]}"]')
    assert series_link.count() == 1

    with page.expect_navigation(wait_until="networkidle"):
        featured_section.get_by_role("link", name="View matching issues").click()

    page.get_by_role("heading", name="Advanced Search").wait_for()
    assert "filters=" in page.url
    assert "edit_promoted_rail" not in page.url
    assert page.locator('input[type="text"]:visible').first.input_value() == "Smoke"


@pytest.mark.browser
@pytest.mark.parametrize(
    ("rail_key", "heading", "kind"),
    [
        ("recently_added_series", "Recently Added Series", "added"),
        ("recently_updated_series", "Recently Updated Series", "updated"),
    ],
)
def test_recent_series_rail_view_all_opens_the_matching_series_browser(
        page,
        browser_server,
        rail_key,
        heading,
        kind,
):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")
    rail = page.locator(f'[data-home-rail="{rail_key}"]')
    rail.get_by_role("heading", name=heading).wait_for()

    with page.expect_navigation(wait_until="networkidle"):
        rail.get_by_role("link", name="View All", exact=True).click()

    page.get_by_role("heading", name=heading, exact=True).wait_for()
    assert page.url.endswith(f"/series/recent/{kind}")
    page.locator(f'a[href="/series/{seed["series_id"]}"]').wait_for()


@pytest.mark.browser
def test_pinned_reading_list_card_opens_reader_and_unpins(page, browser_server):
    seed = browser_server["seed"]
    list_url = f"{browser_server['base_url']}/reading-lists/{seed['reading_list_id']}"

    page.goto(list_url, wait_until="networkidle")
    page.get_by_role("heading", name=seed["reading_list_name"]).wait_for()
    with page.expect_response(lambda response: response.url.endswith("/pin") and response.request.method == "POST"):
        page.get_by_role("button", name="Pin to Home").click()
    page.get_by_role("button", name="Pinned to Home").wait_for()

    page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")
    page.get_by_role("heading", name="Pinned Reading Lists").wait_for()
    card = page.get_by_test_id("pinned-reading-list-card")
    assert card.count() == 1
    assert card.get_by_role("link", name=seed["reading_list_name"]).is_visible()
    assert card.get_by_text("1 / 3 read").is_visible()

    card.get_by_title(f"Continue {seed['reading_list_name']}").click()
    page.wait_for_url(f"**/reader/{seed['in_progress_comic_id']}*")
    page.locator(".reader-container").wait_for()
    page.locator(".nav-zone.center").click()
    page.locator(".reader-toolbar").filter(has_text=seed["reading_list_name"]).first.wait_for()

    page.goto(list_url, wait_until="networkidle")
    with page.expect_response(lambda response: response.url.endswith("/pin") and response.request.method == "DELETE"):
        page.get_by_role("button", name="Pinned to Home").click()
    page.get_by_role("button", name="Pin to Home").wait_for()

    page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")
    page.get_by_role("heading", name="Jump Back In").wait_for()
    assert not page.get_by_role("heading", name="Pinned Reading Lists").is_visible()


@pytest.mark.browser
def test_home_shift_click_range_stays_within_one_rail(page, browser_server, browser_db_factory):
    seed = browser_server["seed"]
    # Jump Back In lists in-progress comics, most recently read first: #1, #2, #3.
    rail_order = [seed["completed_comic_id"], seed["active_comic_id"], seed["in_progress_comic_id"]]
    rated_comic_id = seed["active_comic_id"]

    session = browser_db_factory()
    try:
        session.query(ReadingProgress).delete()
        now = datetime.now(timezone.utc)
        for minutes_ago, comic_id in enumerate(rail_order):
            session.add(
                ReadingProgress(
                    user_id=seed["user_id"],
                    comic_id=comic_id,
                    current_page=1,
                    total_pages=3,
                    completed=False,
                    last_read_at=now - timedelta(minutes=minutes_ago),
                )
            )
        session.get(Comic, rated_comic_id).community_rating = 4.5
        session.commit()

        page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")
        jump_back_in = page.locator("section").filter(has=page.get_by_role("heading", name="Jump Back In"))
        acclaimed = page.locator("section").filter(has=page.get_by_role("heading", name="Critically Acclaimed"))

        def card(rail, comic_id):
            return rail.locator(f'[data-selection-card][data-comic-id="{comic_id}"]')

        def selected_ids():
            return sorted(page.evaluate("Array.from(Alpine.store('batch').payload.comic_ids)"))

        card(jump_back_in, rail_order[0]).get_by_test_id("comic-select-toggle").click()
        card(jump_back_in, rail_order[2]).locator("a").first.click(modifiers=["Shift"])
        assert selected_ids() == sorted(rail_order)
        assert page.url == f"{browser_server['base_url']}/"

        # The last comic clicked is in Jump Back In, so a shift-click in another rail selects only that comic.
        page.evaluate("Alpine.store('batch').clear()")
        card(jump_back_in, rail_order[0]).get_by_test_id("comic-select-toggle").click()
        card(acclaimed, rated_comic_id).locator("a").first.click(modifiers=["Shift"])
        assert selected_ids() == sorted([rail_order[0], rated_comic_id])
    finally:
        session.get(Comic, rated_comic_id).community_rating = None
        session.commit()
        session.close()
