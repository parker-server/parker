from datetime import datetime, timezone

import pytest

from app.core import setting_keys
from app.core.security import create_access_token
from app.core.templates import templates
from app.models.comic import Volume
from app.models.interactions import UserVolumeFollow
from app.models.notification import Notification
from app.models.setting import SystemSetting
from app.models.user import User
from app.services.notifications.producers import ScanInfo, notify_follow_arrivals
from app.services.notifications.service import NotificationService


@pytest.fixture
def notifications_state(browser_server, monkeypatch):
    """Start each test with no notifications, the feature on and a signed-in browser session."""
    session = browser_server["db_factory"]()
    try:
        session.query(Notification).delete()
        session.query(SystemSetting).filter(SystemSetting.key == setting_keys.FEATURES_NOTIFICATIONS_ENABLED).delete()
        user = session.get(User, browser_server["seed"]["user_id"])
        user.notification_preferences = None
        username = user.username
        session.commit()
    finally:
        session.close()

    # base.html reads this setting through the cached helper, which bypasses the
    # browser test database, so pin it on.
    original = templates.env.globals["get_system_setting"]
    monkeypatch.setitem(
        templates.env.globals,
        "get_system_setting",
        lambda key, default=None: True if key == setting_keys.FEATURES_NOTIFICATIONS_ENABLED else original(key, default),
    )
    return {"token": create_access_token(username)}


def _sign_in(page, token):
    page.add_init_script(f"localStorage.setItem('parker.token', {token!r});")


def _notify(browser_server, group_key, first_title="Notifications", count=1):
    session = browser_server["db_factory"]()
    try:
        NotificationService(session).notify(
            "whats_new",
            [browser_server["seed"]["user_id"]],
            data={"version": "0.1.48", "first_title": first_title},
            group_key=group_key,
            count=count,
        )
    finally:
        session.close()


def _set_enabled(browser_server, enabled):
    session = browser_server["db_factory"]()
    try:
        session.add(SystemSetting(
            key=setting_keys.FEATURES_NOTIFICATIONS_ENABLED,
            value="true" if enabled else "false",
            category="features",
            data_type="bool",
        ))
        session.commit()
    finally:
        session.close()


@pytest.mark.browser
def test_bell_shows_count_and_opens_notification(page, browser_server, notifications_state):
    _notify(browser_server, "a", first_title="Notifications", count=2)
    _sign_in(page, notifications_state["token"])

    page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")
    count = page.locator("[data-testid='notification-count']")
    count.wait_for(state="visible")
    assert count.inner_text() == "1"

    page.locator("[data-testid='notification-bell']").click()
    item = page.locator("[data-testid='notification-item']")
    item.wait_for(state="visible")
    assert "What's new in Parker 0.1.48" in item.inner_text()
    assert "Notifications, and 1 more" in item.inner_text()

    item.click()
    page.wait_for_url("**/whats-new")
    page.locator("text=What's New in Parker").wait_for()
    page.wait_for_load_state("networkidle")
    assert page.locator("[data-testid='notification-count']").is_hidden()


@pytest.mark.browser
def test_follow_arrival_opens_the_volume(page, browser_server, notifications_state):
    seed = browser_server["seed"]
    session = browser_server["db_factory"]()
    try:
        volume = session.get(Volume, seed["volume_id"])
        follow = UserVolumeFollow(user_id=seed["user_id"], volume_id=volume.id)
        session.add(follow)
        session.commit()
        # Every seeded issue counts as new for a scan that started before they were added.
        notify_follow_arrivals(session, ScanInfo(
            library_id=volume.series.library_id,
            started_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
            first_scan=False,
        ))
    finally:
        session.close()

    try:
        _sign_in(page, notifications_state["token"])
        page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")
        page.locator("[data-testid='notification-count']").wait_for(state="visible")

        page.locator("[data-testid='notification-bell']").click()
        item = page.locator("[data-testid='notification-item']")
        item.wait_for(state="visible")
        assert f"new issues in {seed['series_name']} Vol 1" in item.inner_text()

        item.click()
        page.wait_for_url(f"**/volumes/{seed['volume_id']}")
        page.wait_for_load_state("networkidle")
        assert page.locator("[data-testid='notification-count']").is_hidden()
    finally:
        session = browser_server["db_factory"]()
        try:
            session.query(UserVolumeFollow).filter_by(user_id=seed["user_id"], volume_id=seed["volume_id"]).delete()
            session.commit()
        finally:
            session.close()


@pytest.mark.browser
def test_notifications_page_mark_all_read_and_clear(page, browser_server, notifications_state):
    _notify(browser_server, "a", first_title="First")
    _notify(browser_server, "b", first_title="Second")
    _sign_in(page, notifications_state["token"])

    page.goto(f"{browser_server['base_url']}/user/notifications", wait_until="networkidle")
    rows = page.locator("[data-testid='notification-row']")
    rows.first.wait_for()
    assert rows.count() == 2

    page.get_by_role("button", name="Mark all read").click()
    page.locator("[data-testid='notification-count']").wait_for(state="hidden")
    page.get_by_role("tab", name="Unread").click()
    page.locator("text=No unread notifications").wait_for()

    page.get_by_role("tab", name="All").click()
    rows.first.wait_for()
    page.get_by_role("button", name="Clear read").click()
    page.get_by_role("button", name="Clear", exact=True).click()
    page.locator("text=No notifications yet").wait_for()


@pytest.mark.browser
def test_turning_off_a_kind_in_settings_is_saved(page, browser_server, notifications_state):
    _sign_in(page, notifications_state["token"])

    page.goto(f"{browser_server['base_url']}/user/settings#notifications", wait_until="networkidle")
    checkbox = page.locator("#notification_kind_whats_new")
    checkbox.wait_for()
    assert checkbox.is_checked()

    with page.expect_response("**/api/notifications/preferences") as response:
        checkbox.click()
    assert response.value.ok

    _notify(browser_server, "a")
    session = browser_server["db_factory"]()
    try:
        assert session.query(Notification).count() == 0
    finally:
        session.close()


@pytest.mark.browser
def test_bell_hidden_when_notifications_are_off(page, browser_server, notifications_state):
    _set_enabled(browser_server, False)
    _sign_in(page, notifications_state["token"])

    page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")

    assert page.locator("[data-testid='notification-bell']").is_hidden()


@pytest.mark.browser
def test_bell_dropdown_fits_phone_screen(mobile_page, browser_server, notifications_state):
    _notify(browser_server, "a")
    _sign_in(mobile_page, notifications_state["token"])

    mobile_page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")
    mobile_page.locator("[data-testid='notification-bell']").click()
    dropdown = mobile_page.locator("[data-testid='notification-dropdown']")
    dropdown.wait_for(state="visible")
    mobile_page.wait_for_timeout(200)

    box = dropdown.bounding_box()
    viewport = mobile_page.viewport_size
    assert box["x"] >= 0
    assert box["x"] + box["width"] <= viewport["width"]
