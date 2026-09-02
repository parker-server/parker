import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.comic import Comic, Volume
from app.models.interactions import UserVolumeFollow
from app.models.series import Series
from app.models.tags import Character
from tests.factories import create_comic


FITTING_SUMMARY = (
    "On a station in the depths of space, a host of alien facehuggers have just been hatched, "
    "destined to implant into humans and birth even deadlier Xenomorphs. But not all facehuggers "
    "have such ill intent! Meet Facehuggie, the friendliest Facehugger!"
)

LONG_SUMMARY = (
    "A sprawling space-horror synopsis follows a salvage crew through a station full of sealed "
    "doors, bad choices, and worse biological surprises. The story tracks every desperate turn as "
    "the survivors realize the outbreak has already moved through the vents, the command deck, and "
    "the only shuttle bay still attached to the station. Every log entry adds another impossible "
    "choice, another sealed corridor, and another reason the crew cannot trust the map, the captain, "
    "or the emergency broadcast promising rescue from just beyond the quarantine line."
)

WIDE_FITTING_SUMMARY = " ".join(["ill"] * 95)


def _set_series_summary(browser_server, summary):
    seed = browser_server["seed"]
    session = browser_server["db_factory"]()
    try:
        series = session.get(Series, seed["series_id"])
        series.summary_override = summary
        session.commit()
    finally:
        session.close()


def _set_volume_summary(browser_server, summary):
    seed = browser_server["seed"]
    session = browser_server["db_factory"]()
    try:
        volume = session.get(Volume, seed["volume_id"])
        volume.summary_override = summary
        session.commit()
    finally:
        session.close()


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


def _add_extra_detail_characters(browser_server):
    seed = browser_server["seed"]
    session = browser_server["db_factory"]()
    names = [f"ZZ Lazy Detail Character {index:02d}" for index in range(26)]
    try:
        comic = session.get(Comic, seed["in_progress_comic_id"])
        characters = [Character(name=name) for name in names]
        comic.characters.extend(characters)
        session.add_all(characters)
        session.commit()
        return names
    finally:
        session.close()


def _remove_extra_detail_characters(browser_server, names):
    session = browser_server["db_factory"]()
    try:
        characters = session.scalars(select(Character).where(Character.name.in_(names))).all()
        for character in characters:
            character.comics.clear()
            session.delete(character)
        session.commit()
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
def test_search_operator_options_match_selected_field(page, browser_server):
    page.goto(f"{browser_server['base_url']}/search", wait_until="networkidle")

    page.get_by_role("heading", name="Advanced Search").wait_for()

    first_rule = page.locator(".space-y-4 > div").first
    field_select = first_rule.locator("select").nth(0)
    operator_select = first_rule.locator("select").nth(1)

    def operator_values():
        return operator_select.locator("option").evaluate_all(
            "(options) => options.map((option) => option.value)"
        )

    field_select.select_option("year")
    assert operator_values() == ["equal", "not_equal", "at_least", "at_most", "is_empty", "is_not_empty"]
    assert operator_select.input_value() == "equal"
    year_input = first_rule.locator("input[x-model='rule.value'][type='number']")
    assert year_input.get_attribute("step") == "1"
    assert year_input.get_attribute("max") in (None, "")

    field_select.select_option("series")
    assert operator_values() == ["equal", "not_equal", "contains", "does_not_contain"]

    field_select.select_option("library")
    assert operator_values() == ["equal", "not_equal", "contains", "does_not_contain"]

    field_select.select_option("language")
    assert operator_values() == ["equal", "not_equal", "contains", "does_not_contain", "is_empty", "is_not_empty"]

    field_select.select_option("writer")
    assert operator_values() == [
        "equal",
        "not_equal",
        "contains",
        "does_not_contain",
        "must_contain",
        "is_empty",
        "is_not_empty",
    ]


@pytest.mark.browser
def test_search_sort_field_selects_natural_default_order(page, browser_server):
    page.goto(f"{browser_server['base_url']}/search", wait_until="networkidle")

    page.get_by_role("heading", name="Advanced Search").wait_for()

    first_rule = page.locator(".space-y-4 > div").first
    first_rule.locator("select").nth(0).select_option("title")
    first_rule.locator("input[x-model='rule.value'][type='text']").fill("Smoke")

    sort_select = page.locator("select[x-model='sortBy']")
    order_select = page.locator("select[x-model='sortOrder']")

    sort_select.select_option("page_count")
    assert order_select.input_value() == "desc"

    sort_select.select_option("title")
    assert order_select.input_value() == "asc"

    sort_select.select_option("series")
    assert order_select.input_value() == "asc"

    with page.expect_response(
        lambda response: "/api/comics/search" in response.url and response.request.method == "POST"
    ) as response_info:
        page.get_by_role("button", name="Search Comics").click()

    response = response_info.value
    payload = json.loads(response.request.post_data or "{}")

    assert response.status == 200
    assert payload["sort_by"] == "series"
    assert payload["sort_order"] == "asc"

    order_select.select_option("desc")

    with page.expect_response(
        lambda response: "/api/comics/search" in response.url and response.request.method == "POST"
    ) as response_info:
        page.get_by_role("button", name="Search Comics").click()

    response = response_info.value
    payload = json.loads(response.request.post_data or "{}")

    assert response.status == 200
    assert payload["sort_by"] == "series"
    assert payload["sort_order"] == "desc"


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
def test_series_and_volume_details_tabs_lazy_load_metadata(page, browser_server):
    seed = browser_server["seed"]
    request_urls = []
    page.on("request", lambda request: request_urls.append(request.url))

    series_details_path = f"/api/series/{seed['series_id']}/details"
    page.goto(f"{browser_server['base_url']}/series/{seed['series_id']}", wait_until="networkidle")
    page.wait_for_selector(f"text={seed['series_name']}")
    assert not any(series_details_path in url for url in request_urls)

    page.get_by_role("button", name="Details").click()
    page.wait_for_selector("text=Captain Smoke")
    page.wait_for_selector("text=Casey Smoke")
    assert any(series_details_path in url for url in request_urls)

    request_urls.clear()
    volume_details_path = f"/api/volumes/{seed['volume_id']}/details"
    page.goto(f"{browser_server['base_url']}/volumes/{seed['volume_id']}", wait_until="networkidle")
    page.get_by_role("heading", name="Volume 1").wait_for()
    assert not any(volume_details_path in url for url in request_urls)

    page.get_by_role("button", name="Details").click()
    page.wait_for_selector("text=Captain Smoke")
    page.wait_for_selector("text=Casey Smoke")
    assert any(volume_details_path in url for url in request_urls)


@pytest.mark.browser
def test_series_details_show_less_removes_expanded_metadata_chips(page, browser_server):
    seed = browser_server["seed"]
    names = _add_extra_detail_characters(browser_server)
    target_name = names[-1]

    try:
        page.goto(f"{browser_server['base_url']}/series/{seed['series_id']}", wait_until="networkidle")
        page.get_by_role("button", name="Details").click()

        page.get_by_role("button", name="Show 2 more").click()
        page.get_by_text(target_name).wait_for()

        page.get_by_role("button", name="Show Less").click()
        page.get_by_text(target_name).wait_for(state="detached")
        page.get_by_role("button", name="Show 2 more").wait_for()
    finally:
        _remove_extra_detail_characters(browser_server, names)


@pytest.mark.browser
def test_series_detail_read_more_appears_for_long_summary(page, browser_server):
    seed = browser_server["seed"]
    _set_series_summary(browser_server, LONG_SUMMARY)

    page.goto(f"{browser_server['base_url']}/series/{seed['series_id']}", wait_until="networkidle")

    page.get_by_role("button", name="Read More").wait_for()


@pytest.mark.browser
def test_series_detail_read_more_stays_hidden_when_long_summary_fits(page, browser_server):
    assert len(WIDE_FITTING_SUMMARY) > 280
    seed = browser_server["seed"]
    _set_series_summary(browser_server, WIDE_FITTING_SUMMARY)

    page.set_viewport_size({"width": 1600, "height": 900})
    page.goto(f"{browser_server['base_url']}/series/{seed['series_id']}", wait_until="networkidle")

    synopsis = page.locator("[data-series-synopsis]")
    synopsis.wait_for()
    page.wait_for_function(
        """
        () => {
            const el = document.querySelector('[data-series-synopsis]');
            return el && el.textContent.length > 280;
        }
        """
    )
    assert not page.get_by_role("button", name="Read More").is_visible()


@pytest.mark.browser
def test_series_detail_read_more_stays_hidden_for_short_summary(page, browser_server):
    seed = browser_server["seed"]
    _set_series_summary(browser_server, FITTING_SUMMARY)

    page.goto(f"{browser_server['base_url']}/series/{seed['series_id']}", wait_until="networkidle")

    page.get_by_text(FITTING_SUMMARY).wait_for()
    assert not page.get_by_role("button", name="Read More").is_visible()


@pytest.mark.browser
def test_volume_detail_read_more_appears_for_long_summary(page, browser_server):
    seed = browser_server["seed"]
    _set_volume_summary(browser_server, LONG_SUMMARY)

    page.goto(f"{browser_server['base_url']}/volumes/{seed['volume_id']}", wait_until="networkidle")

    page.get_by_role("button", name="Read More").wait_for()


@pytest.mark.browser
def test_volume_detail_read_more_stays_hidden_for_short_summary(page, browser_server):
    seed = browser_server["seed"]
    _set_volume_summary(browser_server, FITTING_SUMMARY)

    page.goto(f"{browser_server['base_url']}/volumes/{seed['volume_id']}", wait_until="networkidle")

    page.get_by_text(FITTING_SUMMARY).wait_for()
    assert not page.get_by_role("button", name="Read More").is_visible()


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
