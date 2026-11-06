import pytest

from app.models.user import User
from app.services.settings_service import SettingsService


def _make_admin(browser_server) -> None:
    session = browser_server["db_factory"]()
    try:
        SettingsService(session).initialize_defaults()
        user = session.get(User, browser_server["seed"]["user_id"])
        user.is_superuser = True
        session.commit()
    finally:
        session.close()


def _wait_until_scrolled_into_view(page, selector: str) -> None:
    # Smooth scrolling takes a moment; wait for the element's top to settle near the top of the viewport.
    page.wait_for_function(
        """(selector) => {
            const el = document.querySelector(selector);
            if (!el || !el.offsetParent) return false;
            const top = el.getBoundingClientRect().top;
            return top >= 0 && top < window.innerHeight / 2;
        }""",
        arg=selector,
    )


@pytest.mark.browser
def test_settings_deep_link_opens_collapsed_section_and_highlights_setting(mobile_page, browser_server):
    _make_admin(browser_server)
    page = mobile_page

    page.goto(f"{browser_server['base_url']}/admin/settings#server.update_check_enabled", wait_until="networkidle")

    # On phones every section but the first starts collapsed; the deep link opens the right one.
    setting = page.locator('[id="setting-server.update_check_enabled"]')
    setting.wait_for(state="visible")
    _wait_until_scrolled_into_view(page, '[id="setting-server.update_check_enabled"]')
    assert "ring-2" in setting.get_attribute("class")
    assert setting.get_by_text("Check for Updates").is_visible()

    # The highlight fades after a few seconds.
    page.wait_for_function(
        """() => !document.getElementById('setting-server.update_check_enabled').classList.contains('ring-2')""",
        timeout=8000,
    )


@pytest.mark.browser
def test_settings_hash_change_jumps_to_section(page, browser_server):
    _make_admin(browser_server)

    page.goto(f"{browser_server['base_url']}/admin/settings", wait_until="networkidle")
    page.locator("#settings-category-system").wait_for()

    page.evaluate("() => { window.location.hash = 'system'; }")

    _wait_until_scrolled_into_view(page, "#settings-category-system")


@pytest.mark.browser
def test_settings_deep_link_ignores_unknown_targets(page, browser_server):
    _make_admin(browser_server)

    page.goto(f"{browser_server['base_url']}/admin/settings#not.a.setting", wait_until="networkidle")
    page.locator("#settings-category-server").wait_for()

    assert page.evaluate("() => window.scrollY") == 0
    assert page.locator(".ring-2").count() == 0
