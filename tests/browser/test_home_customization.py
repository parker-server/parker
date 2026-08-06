import pytest


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
