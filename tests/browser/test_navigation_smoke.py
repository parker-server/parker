import pytest

from app.models.collection import Collection, CollectionItem
from app.models.comic import Comic
from app.models.tags import Character


def _insert_decade_timeline_fixture(browser_server):
    session = browser_server["db_factory"]()
    try:
        character = Character(name="Decade Smoke")
        session.add(character)
        session.flush()

        volume_id = browser_server["seed"]["volume_id"]
        for offset, year in enumerate(range(2000, 2013), start=1):
            comic = Comic(
                volume_id=volume_id,
                number=str(100 + offset),
                title=f"Decade Smoke {year}",
                year=year,
                filename=f"decade-smoke-{year}.cbz",
                file_path=f"/tmp/decade-smoke-{year}.cbz",
                page_count=3,
            )
            comic.characters.append(character)
            session.add(comic)

        session.commit()
    finally:
        session.close()


def _add_duplicate_collection_context(browser_server):
    session = browser_server["db_factory"]()
    try:
        comic = session.get(Comic, browser_server["seed"]["in_progress_comic_id"])
        comic.series_group = "Smoke Group"
        collection = Collection(name="Smoke Group", description="Generated from SeriesGroup")
        session.add(collection)
        session.flush()
        session.add(CollectionItem(collection_id=collection.id, comic_id=comic.id))
        session.commit()
    finally:
        session.close()


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


@pytest.mark.browser
def test_library_timeline_deep_link_shows_character_history_and_search_handoff(page, browser_server):
    seed = browser_server["seed"]
    page.goto(
        f"{browser_server['base_url']}/timelines?type=character&name=Captain%20Smoke",
        wait_until="networkidle",
    )

    page.get_by_role("heading", name="Captain Smoke").wait_for()
    page.get_by_text("Generated from metadata embedded in your comic files.").wait_for()
    page.locator("h3").filter(has_text=f"{seed['series_name']} #{seed['in_progress_comic_number']}").wait_for()

    page.get_by_role("link", name="View Matching Issues").click()

    page.wait_for_url("**/search*")
    page.get_by_role("heading", name="Advanced Search").wait_for()


@pytest.mark.browser
def test_character_tag_chip_opens_timeline_before_search_handoff(page, browser_server):
    seed = browser_server["seed"]
    page.goto(
        f"{browser_server['base_url']}/comics/{seed['in_progress_comic_id']}",
        wait_until="networkidle",
    )

    page.get_by_role("heading", name=f"{seed['series_name']} #{seed['in_progress_comic_number']}").wait_for()
    page.get_by_role("link", name="Captain Smoke").click()

    page.wait_for_url("**/timelines?type=character&name=Captain%20Smoke")
    page.get_by_role("heading", name="Captain Smoke").wait_for()
    page.locator("h3").filter(has_text=f"{seed['series_name']} #{seed['in_progress_comic_number']}").wait_for()

    page.get_by_role("link", name="View Matching Issues").click()

    page.wait_for_url("**/search*")
    page.get_by_role("heading", name="Advanced Search").wait_for()


@pytest.mark.browser
def test_timeline_subject_type_toggle_clears_search_input(page, browser_server):
    page.goto(f"{browser_server['base_url']}/timelines", wait_until="networkidle")

    search_input = page.get_by_placeholder("Search characters or teams")
    search_input.fill("Captain")

    page.get_by_role("button", name="Team").click()

    assert search_input.input_value() == ""
    page.get_by_role("heading", name="Character and Team History").wait_for()


@pytest.mark.browser
def test_timeline_entry_deduplicates_matching_series_group_and_collection_badges(page, browser_server):
    seed = browser_server["seed"]
    _add_duplicate_collection_context(browser_server)

    page.goto(
        f"{browser_server['base_url']}/timelines?type=character&name=Captain%20Smoke",
        wait_until="networkidle",
    )

    page.get_by_role("heading", name="Captain Smoke").wait_for()
    row = page.locator("[data-timeline-entry-card]").filter(
        has_text=f"{seed['series_name']} #{seed['in_progress_comic_number']}"
    ).first
    row.wait_for()

    assert row.get_by_text("Smoke Group").count() == 1


@pytest.mark.browser
def test_library_timeline_groups_long_histories_by_decade(page, browser_server):
    _insert_decade_timeline_fixture(browser_server)

    page.goto(
        f"{browser_server['base_url']}/timelines?type=character&name=Decade%20Smoke",
        wait_until="networkidle",
    )

    page.get_by_role("heading", name="Decade Smoke").wait_for()
    page.get_by_text("grouping it by decade").wait_for()
    page.get_by_text("2000s").wait_for()
    page.get_by_text("2010s").wait_for()

    issue_heading = page.locator("h3").filter(has_text="Smoke Series #101")
    assert issue_heading.count() == 0 or not issue_heading.first.is_visible()

    page.get_by_role("button").filter(has_text="2000s").click()

    issue_heading.first.wait_for()
