from contextlib import contextmanager

import pytest

from app.api import insights
from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.tags import Character


@contextmanager
def _co_appearing_characters(browser_db_factory, browser_seed_data, character_count: int):
    """Adds two comics that share the same characters, so every pair has two shared issues."""
    session = browser_db_factory()
    volume = session.get(Volume, browser_seed_data["volume_id"])
    root_id = session.query(Library).first().active_root.id
    characters = [Character(name=f"Chemistry Hero {index:02d}") for index in range(character_count)]
    comics = [
        Comic(
            volume=volume,
            library_root_id=root_id,
            relative_path=f"chemistry-{index}.cbz",
            number=f"chemistry-{index}",
            filename=f"chemistry-{index}.cbz",
            page_count=1,
            characters=list(characters),
        )
        for index in range(2)
    ]
    session.add_all(characters + comics)
    session.commit()
    insights._payload_cache.clear()
    try:
        yield
    finally:
        for item in comics + characters:
            session.delete(item)
        session.commit()
        session.close()
        insights._payload_cache.clear()


def _grid_metrics(page, base_url: str) -> dict:
    page.goto(f"{base_url}/insights/character-chemistry", wait_until="networkidle")
    page.get_by_role("heading", name="Top Team-Ups").wait_for()
    return page.evaluate(
        """() => {
            const grid = document.querySelector('table').parentElement;
            const lastTeamUp = [...document.querySelectorAll('aside button')].pop();
            return {
                gridHeight: grid.clientHeight,
                gridContentHeight: grid.scrollHeight,
                gridBottom: grid.getBoundingClientRect().bottom,
                lastTeamUpBottom: lastTeamUp.getBoundingClientRect().bottom,
            };
        }"""
    )


@pytest.mark.browser
def test_chemistry_grid_stays_in_view_beside_top_team_ups_on_desktop(
        page,
        browser_server,
        browser_db_factory,
        browser_seed_data,
):
    page.set_viewport_size({"width": 1440, "height": 900})

    with _co_appearing_characters(browser_db_factory, browser_seed_data, 12):
        metrics = _grid_metrics(page, browser_server["base_url"])

        # Scroll the page to the last Top Team-Ups card; the grid should follow
        # so its column headers stay visible just below the nav bar.
        scrolled = page.evaluate(
            """() => {
                const lastTeamUp = [...document.querySelectorAll('aside button')].pop();
                window.scrollBy(0, lastTeamUp.getBoundingClientRect().bottom - window.innerHeight);
                const grid = document.querySelector('table').parentElement.getBoundingClientRect();
                const header = document.querySelector('thead th:last-child').getBoundingClientRect();
                const nav = document.querySelector('nav').getBoundingClientRect();
                return {gridTop: grid.top, gridBottom: grid.bottom, headerTop: header.top, navBottom: nav.bottom};
            }"""
        )

    # The grid uses more than the old 70% of the window but still fits on
    # screen, scrolling inside for the rest.
    assert metrics["gridHeight"] > 900 * 0.7
    assert metrics["gridContentHeight"] > metrics["gridHeight"]
    assert scrolled["gridTop"] >= scrolled["navBottom"]
    assert scrolled["gridBottom"] <= 900
    assert scrolled["navBottom"] <= scrolled["headerTop"] < 900


@pytest.mark.browser
def test_small_chemistry_grid_is_not_stretched_or_cut_off(
        page,
        browser_server,
        browser_db_factory,
        browser_seed_data,
):
    page.set_viewport_size({"width": 1440, "height": 900})

    with _co_appearing_characters(browser_db_factory, browser_seed_data, 2):
        metrics = _grid_metrics(page, browser_server["base_url"])

    assert metrics["gridHeight"] == metrics["gridContentHeight"]


@pytest.mark.browser
def test_chemistry_grid_keeps_its_scroll_height_on_phones(
        mobile_page,
        browser_server,
        browser_db_factory,
        browser_seed_data,
):
    with _co_appearing_characters(browser_db_factory, browser_seed_data, 12):
        metrics = _grid_metrics(mobile_page, browser_server["base_url"])

    assert metrics["gridHeight"] <= round(844 * 0.7)
    assert metrics["gridContentHeight"] > metrics["gridHeight"]
