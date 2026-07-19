from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, create_refresh_token
from app.models.bookmark import Bookmark
from app.models.pull_list import PullList, PullListItem
from app.models.reading_progress import ReadingProgress


def _create_pull_list(db_factory, user_id, name, description=None):
    session = db_factory()
    try:
        pull_list = PullList(user_id=user_id, name=name, description=description)
        session.add(pull_list)
        session.commit()
        session.refresh(pull_list)
        return pull_list.id
    finally:
        session.close()


def _get_pull_list_snapshot(db_factory, name):
    session = db_factory()
    try:
        pull_list = session.scalar(select(PullList).where(PullList.name == name))
        if pull_list is None:
            return None

        items = session.scalars(
            select(PullListItem).where(PullListItem.pull_list_id == pull_list.id)
        ).all()
        return {
            "id": pull_list.id,
            "description": pull_list.description,
            "comic_ids": [item.comic_id for item in items],
        }
    finally:
        session.close()


def _add_comic_to_pull_list(db_factory, pull_list_id, comic_id):
    session = db_factory()
    try:
        session.add(PullListItem(pull_list_id=pull_list_id, comic_id=comic_id, sort_order=0))
        session.commit()
    finally:
        session.close()


@pytest.mark.browser
def test_login_next_redirect_and_logout_clears_session(page, browser_server):
    page.goto(f"{browser_server['base_url']}/login?next=/search", wait_until="networkidle")

    page.locator("#username").fill("browser-user")
    page.locator("#password").fill("browser-pass")
    page.get_by_role("button", name="Sign in").click()

    page.wait_for_url("**/search")
    page.get_by_role("heading", name="Advanced Search").wait_for()

    session_state = page.evaluate(
        """
        () => ({
            token: localStorage.getItem('token'),
            refreshToken: localStorage.getItem('refresh_token'),
            hasAccessCookie: document.cookie.includes('access_token=')
        })
        """
    )
    assert session_state["token"]
    assert session_state["refreshToken"]
    assert session_state["hasAccessCookie"] is True

    page.get_by_role("button", name="Logout").click()

    page.wait_for_url("**/login")
    page.get_by_role("button", name="Sign in").wait_for()

    cleared_state = page.evaluate(
        """
        () => ({
            token: localStorage.getItem('token'),
            refreshToken: localStorage.getItem('refresh_token'),
            hasAccessCookie: document.cookie.includes('access_token=')
        })
        """
    )
    assert cleared_state["token"] is None
    assert cleared_state["refreshToken"] is None
    assert cleared_state["hasAccessCookie"] is False


@pytest.mark.browser
def test_session_refresh_resyncs_access_cookie_before_navigation(page, browser_server):
    expired_access_token = create_access_token(
        subject="browser-user",
        expires_delta=timedelta(seconds=-30),
    )
    refresh_token = create_refresh_token(subject="browser-user")

    page.goto(f"{browser_server['base_url']}/search", wait_until="networkidle")

    page.evaluate(
        """
        ({ expiredAccessToken, refreshToken }) => {
            localStorage.setItem('token', expiredAccessToken);
            localStorage.setItem('refresh_token', refreshToken);
            document.cookie = 'access_token=; path=/; max-age=0; SameSite=Lax';
        }
        """,
        {
            "expiredAccessToken": expired_access_token,
            "refreshToken": refresh_token,
        },
    )

    refreshed_token = page.evaluate(
        """
        async () => {
            await window.parker.auth.refreshSession({ force: true });
            return localStorage.getItem('token');
        }
        """
    )

    session_state = page.evaluate(
        """
        () => ({
            token: localStorage.getItem('token'),
            refreshToken: localStorage.getItem('refresh_token'),
            hasAccessCookie: document.cookie.includes('access_token=')
        })
        """
    )
    assert refreshed_token != expired_access_token
    assert session_state["token"] == refreshed_token
    assert session_state["refreshToken"] != refresh_token
    assert session_state["hasAccessCookie"] is True


@pytest.mark.browser
def test_idle_session_clears_instead_of_refreshing(page, browser_server):
    expired_access_token = create_access_token(
        subject="browser-user",
        expires_delta=timedelta(seconds=-30),
    )
    refresh_token = create_refresh_token(subject="browser-user")

    page.goto(f"{browser_server['base_url']}/search", wait_until="networkidle")

    error_message = page.evaluate(
        """
        async ({ expiredAccessToken, refreshToken }) => {
            const staleActivityAt = Math.floor(Date.now() / 1000) - window.parker.auth.idleTimeoutSeconds - 10;
            localStorage.setItem('token', expiredAccessToken);
            localStorage.setItem('refresh_token', refreshToken);
            localStorage.setItem('parker.lastActivityAt', String(staleActivityAt));
            document.cookie = 'access_token=; path=/; max-age=0; SameSite=Lax';

            try {
                await window.parker.auth.refreshSession({ force: true });
            } catch (error) {
                return error.message;
            }
            return null;
        }
        """,
        {
            "expiredAccessToken": expired_access_token,
            "refreshToken": refresh_token,
        },
    )

    session_state = page.evaluate(
        """
        () => ({
            token: localStorage.getItem('token'),
            refreshToken: localStorage.getItem('refresh_token'),
            lastActivityAt: localStorage.getItem('parker.lastActivityAt'),
            hasAccessCookie: document.cookie.includes('access_token=')
        })
        """
    )
    assert error_message == "Session idle timeout"
    assert session_state == {
        "token": None,
        "refreshToken": None,
        "lastActivityAt": None,
        "hasAccessCookie": False,
    }


@pytest.mark.browser
def test_saved_search_can_be_reapplied_from_load_menu(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/search", wait_until="networkidle")

    page.get_by_role("heading", name="Advanced Search").wait_for()

    first_rule = page.locator(".space-y-4 > div").first
    first_rule.locator("select").nth(0).select_option("title")
    rule_input = first_rule.locator("input[x-model='rule.value'][type='text']")
    rule_input.fill(seed["in_progress_comic_title"])

    page.get_by_role("button", name="Save").click()
    page.get_by_role("heading", name="Save Configuration").wait_for()

    page.locator("input[placeholder=\"e.g. 'Alan Moore 80s'\"]").fill("Browser Reapply Search")
    page.get_by_role("button", name="Save to Search Menu").click()

    rule_input.fill(seed["completed_comic_title"])
    page.get_by_role("button", name="Search Comics").click()
    page.locator("div[x-show='hasSearched']").wait_for()
    page.locator("p.text-sm.text-gray-400").filter(has_text=seed["completed_comic_title"]).first.wait_for()

    page.get_by_role("button", name="Load").click()
    saved_row = page.locator("div.cursor-pointer").filter(has_text="Browser Reapply Search").first
    saved_row.wait_for()
    saved_row.click()

    page.wait_for_function(
        f"""
        () => {{
            const input = document.querySelector("input[x-model='rule.value'][type='text']");
            return input && input.value === {seed["in_progress_comic_title"]!r};
        }}
        """
    )
    reapplied_result = page.locator("p.text-sm.text-gray-400").filter(has_text=seed["in_progress_comic_title"]).first
    reapplied_result.wait_for()
    assert reapplied_result.is_visible()


@pytest.mark.browser
def test_comic_detail_add_to_existing_pull_list_then_edit_and_remove_item(page, browser_server):
    seed = browser_server["seed"]
    list_name = "Browser Detail List"
    updated_name = "Browser Detail List Updated"
    updated_description = "Edited from a browser flow test"
    pull_list_id = _create_pull_list(browser_server["db_factory"], seed["user_id"], list_name)

    page.goto(f"{browser_server['base_url']}/comics/{seed['active_comic_id']}", wait_until="networkidle")

    page.get_by_role("heading", name=f"{seed['series_name']} #{seed['active_comic_number']}").wait_for()
    assert page.locator("[data-stack-membership]").count() == 1
    assert page.locator("[data-stack-membership]").is_hidden()
    page.get_by_role("button", name="Add to Stack").click()

    page.get_by_role("heading", name="Add to Stack").wait_for()
    page.get_by_role("button", name=list_name).click()

    page.wait_for_selector("text=Added to stack!")
    page.locator("[data-stack-membership]").wait_for()
    assert page.locator("[data-stack-membership]").text_content().strip() == "(in 1 stack)"

    created_list = _get_pull_list_snapshot(browser_server["db_factory"], list_name)
    assert created_list is not None
    assert seed["active_comic_id"] in created_list["comic_ids"]

    page.goto(f"{browser_server['base_url']}/pull-lists/{pull_list_id}", wait_until="networkidle")
    page.wait_for_url(f"**/stacks/{pull_list_id}")

    page.get_by_role("heading", name=list_name).wait_for()
    page.wait_for_selector(f"text={seed['active_comic_title']}")

    page.locator("button[title='Edit Stack Details']").click()
    page.get_by_role("heading", name="Edit Stack").wait_for()
    edit_name_input = page.locator(".fixed.inset-0.z-50 input[x-ref='editNameInput']").first
    edit_name_input.fill(updated_name)
    edit_desc_input = page.locator(".fixed.inset-0.z-50 input").nth(1)
    edit_desc_input.fill(updated_description)
    page.locator(".fixed.inset-0.z-50").get_by_role("button", name="Save").click()

    page.wait_for_selector("text=Stack updated")
    page.get_by_role("heading", name=updated_name).wait_for()
    updated_list = _get_pull_list_snapshot(browser_server["db_factory"], updated_name)
    assert updated_list is not None
    assert updated_list["description"] == updated_description

    page.locator("button[title='Remove']").click()
    page.get_by_role("heading", name="Remove Item?").wait_for()
    page.wait_for_function(
        """
        () => {
            const active = document.activeElement;
            return !!active && active.tagName === 'BUTTON' && active.textContent.trim() === 'Remove';
        }
        """
    )
    page.keyboard.press("Enter")

    page.wait_for_selector("text=Stack is empty. Go browse and add some comics!")
    cleared_list = _get_pull_list_snapshot(browser_server["db_factory"], updated_name)
    assert cleared_list is not None
    assert cleared_list["comic_ids"] == []


@pytest.mark.browser
def test_comic_detail_shows_existing_stack_membership(page, browser_server):
    seed = browser_server["seed"]
    pull_list_id = _create_pull_list(browser_server["db_factory"], seed["user_id"], "Already Stacked")
    _add_comic_to_pull_list(browser_server["db_factory"], pull_list_id, seed["active_comic_id"])

    page.goto(f"{browser_server['base_url']}/comics/{seed['active_comic_id']}", wait_until="networkidle")

    page.get_by_role("heading", name=f"{seed['series_name']} #{seed['active_comic_number']}").wait_for()
    assert page.locator("[data-stack-membership]").text_content().strip() == "(in 1 stack)"


@pytest.mark.browser
def test_comic_detail_completed_issue_shows_read_again(page, browser_server):
    seed = browser_server["seed"]

    page.goto(f"{browser_server['base_url']}/comics/{seed['completed_comic_id']}", wait_until="networkidle")

    page.get_by_role("heading", name=f"{seed['series_name']} #{seed['completed_comic_number']}").wait_for()
    page.get_by_role("link", name="Read Again").wait_for()


@pytest.mark.browser
def test_comic_detail_bookmarks_launch_reader_detour(page, browser_server):
    seed = browser_server["seed"]
    session = browser_server["db_factory"]()
    try:
        session.add(
            Bookmark(
                user_id=seed["user_id"],
                comic_id=seed["in_progress_comic_id"],
                page_index=0,
                label="Opening beat",
            )
        )
        session.commit()
    finally:
        session.close()

    page.goto(f"{browser_server['base_url']}/comics/{seed['in_progress_comic_id']}", wait_until="networkidle")

    page.get_by_role("heading", name=f"{seed['series_name']} #{seed['in_progress_comic_number']}").wait_for()
    page.get_by_role("heading", name="Bookmarks").wait_for()
    page.locator("[data-detail-bookmark-link]").first.click()

    page.wait_for_url(f"**/reader/{seed['in_progress_comic_id']}**")
    page.wait_for_selector("[data-bookmark-detour]")
    assert page.locator(".reader-controls .text-white.font-bold").first.text_content() == "1"
    assert page.locator("[data-bookmark-detour-return]").text_content().strip() == "Return to page 2"

    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(300)
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(300)
    assert page.locator(".reader-controls .text-white.font-bold").first.text_content() == "3"

    session = browser_server["db_factory"]()
    try:
        progress = session.scalar(
            select(ReadingProgress).where(
                ReadingProgress.user_id == seed["user_id"],
                ReadingProgress.comic_id == seed["in_progress_comic_id"],
            )
        )
    finally:
        session.close()

    assert progress is not None
    assert progress.current_page == 1


@pytest.mark.browser
def test_reader_preferences_persist_across_reload(page, browser_server):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/reader/{seed['active_comic_id']}", wait_until="networkidle")

    page.locator(".reader-container").wait_for()
    page.locator(".nav-zone.center").click()
    page.wait_for_selector(".reader-toolbar")

    page.keyboard.press("d")
    page.keyboard.press("m")
    page.keyboard.press("h")
    page.locator(".reader-controls select").select_option("width")

    page.reload(wait_until="networkidle")
    page.locator(".reader-container").wait_for()

    page.wait_for_function(
        """
        () => {
            const reader = document.querySelector('.reader-container');
            const state = reader?._x_dataStack?.[0];
            return state?.viewMode === 'double'
                && state?.readDirection === 'rtl'
                && state?.fitMode === 'width'
                && state?.uiLocked === true;
        }
        """
    )

    state = page.evaluate(
        """
        () => {
            const reader = document.querySelector('.reader-container');
            const value = reader?._x_dataStack?.[0];
            return {
                viewMode: value?.viewMode,
                readDirection: value?.readDirection,
                fitMode: value?.fitMode,
                uiLocked: value?.uiLocked
            };
        }
        """
    )
    assert state == {
        "viewMode": "double",
        "readDirection": "rtl",
        "fitMode": "width",
        "uiLocked": True,
    }
