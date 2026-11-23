import pytest


def _color(page, selector: str, prop: str) -> str:
    return page.eval_on_selector(selector, f"el => getComputedStyle(el).{prop}")


@pytest.mark.browser
def test_tailwind_gray_and_blue_follow_theme_variables(page, browser_server):
    page.goto(f"{browser_server['base_url']}/insights", wait_until="networkidle")
    eyebrow = "p.text-blue-300"
    card = "a[href$='/timelines']"
    creator_link = "a[href$='/insights/creator-chemistry'] .text-blue-fixed-300"
    timeline_link = "a[href$='/timelines'] .text-gray-300"

    # Defaults match Tailwind's own palette, opacity classes included.
    assert _color(page, "body", "backgroundColor") == "rgb(17, 24, 39)"
    assert _color(page, eyebrow, "color") == "rgb(147, 197, 253)"
    assert _color(page, card, "backgroundColor") == "rgba(31, 41, 55, 0.8)"

    page.evaluate(
        """() => {
            const root = document.documentElement.style;
            root.setProperty('--gray-900', '1 2 3');
            root.setProperty('--gray-800', '4 5 6');
            root.setProperty('--accent-300', '7 8 9');
        }"""
    )

    assert _color(page, "body", "backgroundColor") == "rgb(1, 2, 3)"
    # The card animates color changes, so wait for it to settle.
    page.wait_for_function(
        "([selector, expected]) => getComputedStyle(document.querySelector(selector)).backgroundColor === expected",
        arg=[card, "rgba(4, 5, 6, 0.8)"],
        timeout=2000,
    )
    assert _color(page, eyebrow, "color") == "rgb(7, 8, 9)"
    # Creator chemistry keeps its own blue whatever the accent, and the
    # timeline card stays neutral so no accent matches a chemistry color.
    assert _color(page, creator_link, "color") == "rgb(147, 197, 253)"
    assert _color(page, timeline_link, "color") == "rgb(209, 213, 219)"


def _html_theme(page) -> list[str]:
    return page.evaluate("[document.documentElement.dataset.theme, document.documentElement.dataset.accent]")


def _pick(page, selector: str) -> None:
    with page.expect_response(lambda response: "/api/users/me/preferences" in response.url and response.request.method == "PATCH") as saved:
        page.click(selector)
    assert saved.value.ok


@pytest.mark.browser
def test_picked_theme_applies_right_away_and_on_every_page_but_the_reader(page, browser_server, monkeypatch):
    from app.core import setting_keys
    from app.models.user import User

    base_url, seed = browser_server["base_url"], browser_server["seed"]
    # Template defaults come from the cached settings, which bypass the test database.
    defaults = {
        setting_keys.FEATURES_USER_THEMES_ENABLED: True,
        setting_keys.UI_DEFAULT_THEME: "default",
        setting_keys.UI_DEFAULT_ACCENT: "blue",
    }
    monkeypatch.setattr("app.core.templates.get_cached_setting", lambda key, default=None: defaults.get(key, default))

    try:
        page.goto(f"{base_url}/user/settings", wait_until="networkidle")
        assert _html_theme(page) == ["default", "blue"]

        _pick(page, '[data-theme-option="noir"]')
        _pick(page, '[data-accent-option="red"]')
        assert _html_theme(page) == ["noir", "red"]
        assert page.get_attribute('[data-theme-option="noir"]', "aria-checked") == "true"

        # The server renders the saved theme from now on.
        page.goto(f"{base_url}/series/{seed['series_id']}", wait_until="networkidle")
        assert _html_theme(page) == ["noir", "red"]
        assert page.eval_on_selector("body", "el => getComputedStyle(el).backgroundColor") == "rgb(10, 10, 11)"

        page.goto(f"{base_url}/reader/{seed['active_comic_id']}", wait_until="networkidle")
        assert _html_theme(page) == ["default", "blue"]

        page.goto(f"{base_url}/user/settings", wait_until="networkidle")
        _pick(page, "#appearance >> text=Reset to default")
        assert _html_theme(page) == ["default", "blue"]
        page.reload(wait_until="networkidle")
        assert _html_theme(page) == ["default", "blue"]
    finally:
        session = browser_server["db_factory"]()
        session.get(User, seed["user_id"]).appearance = None
        session.commit()
        session.close()
