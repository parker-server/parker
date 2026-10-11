import pytest
from datetime import date, timedelta

from app.models.promoted_rail import PromotedRail
from app.models.user import User


@pytest.mark.browser
def test_admin_can_create_a_featured_rail_and_save_its_advanced_search_query(
        page,
        browser_server,
        browser_db_factory,
        browser_seed_data,
):
    session = browser_db_factory()
    try:
        user = session.get(User, browser_seed_data["user_id"])
        user.is_superuser = True
        session.commit()
    finally:
        session.close()

    page.goto(f"{browser_server['base_url']}/admin/promoted-rails", wait_until="networkidle")
    page.set_default_timeout(5_000)
    page.get_by_role("heading", name="Featured Rails", exact=True).wait_for()
    page.get_by_role("button", name="New Featured Rail").click()

    form = page.locator("form").filter(has_text="New Featured Rail")
    form.get_by_label("Title").fill("Browser Featured Rail")
    form.get_by_label("Start date").fill("2026-10-01")
    form.get_by_label("End date optional").fill("2026-10-31")

    with page.expect_navigation(wait_until="networkidle"):
        form.get_by_role("button", name="Create and build query").click()

    page.get_by_text("Featured rail", exact=True).wait_for()
    assert "/admin/promoted-rails/" in page.url
    assert page.url.endswith("/query")
    rule_input = page.locator('input[type="text"]:visible').first
    rule_input.fill("Smoke")
    rule_input.press("Enter")

    with page.expect_response(
        lambda response: "/api/promoted-rails/" in response.url
        and response.url.endswith("/query")
        and response.request.method == "PATCH"
    ):
        page.get_by_role("button", name="Save featured query").click()


@pytest.mark.browser
def test_admin_updating_featured_rail_closes_the_modal(
        page,
        browser_server,
        browser_db_factory,
        browser_seed_data,
):
    session = browser_db_factory()
    try:
        user = session.get(User, browser_seed_data["user_id"])
        user.is_superuser = True
        rail = PromotedRail(
            title="Browser Editable Rail",
            starts_on=date.today() - timedelta(days=1),
            ends_on=date.today() + timedelta(days=1),
            enabled=True,
            display_order=0,
        )
        session.add(rail)
        session.commit()
    finally:
        session.close()

    page.goto(f"{browser_server['base_url']}/admin/promoted-rails", wait_until="networkidle")
    row = page.locator("tr").filter(has_text="Browser Editable Rail")
    assert page.locator("thead th").filter(has_text="Order").count() == 1
    assert row.locator("td").nth(1).inner_text() == "0"
    row.get_by_role("button", name="Edit", exact=True).click()

    form = page.locator("form").filter(has_text="Edit Featured Rail")
    form.get_by_label("Title").fill("Updated Browser Editable Rail")
    with page.expect_response(
        lambda response: "/api/promoted-rails/" in response.url
        and response.request.method == "PATCH"
    ):
        form.get_by_role("button", name="Save changes").click()

    form.wait_for(state="hidden")
    page.get_by_text("Updated Browser Editable Rail", exact=True).wait_for()
