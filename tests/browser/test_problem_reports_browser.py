import pytest

from app.models.comic import Comic
from app.models.problem_report import ProblemReport
from app.models.setting import SystemSetting
from app.models.user import User
from app.services.problem_reports import PROBLEM_REPORTS_ENABLED_SETTING, ProblemReportService


@pytest.fixture
def problem_reports_state(browser_server):
    """Start each test with no reports and the feature on."""

    def _reset():
        session = browser_server["db_factory"]()
        try:
            session.query(ProblemReport).delete()
            session.query(SystemSetting).filter(SystemSetting.key == PROBLEM_REPORTS_ENABLED_SETTING).delete()
            session.query(User).filter(User.username.in_(["problem-reporter", "problem-admin"])).delete()
            user = session.get(User, browser_server["seed"]["user_id"])
            user.can_report = True
            user.is_superuser = False
            session.commit()
        finally:
            session.close()

    _reset()
    yield
    _reset()


def _reports(browser_server):
    session = browser_server["db_factory"]()
    try:
        return [
            (report.category, report.page_index, report.message, report.status, report.admin_note)
            for report in session.query(ProblemReport).order_by(ProblemReport.id).all()
        ]
    finally:
        session.close()


def _extra_user(session, username, *, admin=False):
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=admin,
    )
    session.add(user)
    session.commit()
    return user


@pytest.mark.browser
def test_report_from_reader_prefills_current_page(page, browser_server, problem_reports_state):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/reader/{seed['active_comic_id']}?page=1", wait_until="networkidle")
    page.wait_for_selector("img.reader-page")
    page.locator(".nav-zone.center").click()

    page.locator("[data-report-problem-toggle]").click()
    modal = page.locator("[data-problem-report-modal]")
    modal.wait_for(state="visible")
    assert page.locator("[data-problem-report-page]").input_value() == "2"

    page.locator("[data-problem-report-category]").select_option("missing_pages")
    page.locator("[data-problem-report-message]").fill("The splash page is gone")
    # Typing in the modal must not turn reader pages.
    page.keyboard.press("ArrowRight")
    page.locator("[data-problem-report-submit]").click()
    modal.wait_for(state="hidden")

    page.locator("[data-report-problem-toggle] [data-report-problem-dot]").wait_for(state="visible")
    assert _reports(browser_server) == [("missing_pages", 1, "The splash page is gone", "open", None)]

    # Opening it again lists the open report with Withdraw.
    page.locator("[data-report-problem-toggle]").click()
    page.locator("[data-problem-report-open-list]").wait_for(state="visible")
    page.locator("[data-problem-report-withdraw]").click()
    page.locator("[data-problem-report-open-list]").wait_for(state="detached")
    assert _reports(browser_server) == []


@pytest.mark.browser
def test_report_from_comic_page_hides_page_for_metadata(page, browser_server, problem_reports_state):
    seed = browser_server["seed"]
    page.goto(f"{browser_server['base_url']}/comics/{seed['active_comic_id']}", wait_until="networkidle")

    page.locator("[data-report-problem]").click()
    modal = page.locator("[data-problem-report-modal]")
    modal.wait_for(state="visible")
    assert page.locator("[data-problem-report-page]").is_visible()
    page.locator("[data-problem-report-category]").select_option("metadata")
    page.locator("[data-problem-report-page]").wait_for(state="hidden")
    page.locator("[data-problem-report-submit]").click()
    modal.wait_for(state="hidden")

    page.locator("[data-report-problem-dot]").wait_for(state="visible")
    assert _reports(browser_server) == [("metadata", None, None, "open", None)]


@pytest.mark.browser
def test_admin_resolves_with_note_and_reporter_sees_it(page, browser_server, problem_reports_state):
    seed = browser_server["seed"]
    session = browser_server["db_factory"]()
    try:
        reporter = _extra_user(session, "problem-reporter")
        comic = session.get(Comic, seed["active_comic_id"])
        ProblemReportService(session).submit(reporter, comic, "scan_quality", page_index=2, message="Blurry")
        ProblemReportService(session).submit(session.get(User, seed["user_id"]), comic, "broken")
        session.get(User, seed["user_id"]).is_superuser = True
        session.commit()
    finally:
        session.close()

    page.goto(f"{browser_server['base_url']}/admin/problems", wait_until="networkidle")
    groups = page.locator("[data-problem-group]")
    groups.first.wait_for()
    assert groups.count() == 2
    scan_group = groups.filter(has_text="Bad scan quality")
    assert "Page 3" in scan_group.inner_text()
    assert scan_group.locator("[data-problem-open-reader]").get_attribute("href").endswith("page=2")

    # The "Select all" text works as well as its checkbox.
    select_all = page.locator("[data-problems-select-all]")
    select_all.get_by_text("Select all").click()
    page.locator("[data-problems-batch-bar]").wait_for(state="visible")
    assert select_all.locator("input").is_checked()
    select_all.get_by_text("Select all").click()
    page.locator("[data-problems-batch-bar]").wait_for(state="hidden")

    scan_group.locator("[data-problem-resolve]").click()
    page.locator("[data-problems-action-note]").fill("Replaced with a cleaner scan")
    page.locator("[data-problems-action-confirm]").click()
    page.locator("[data-problems-action-modal]").wait_for(state="hidden")
    page.wait_for_function("document.querySelectorAll('[data-problem-group]').length === 1")

    page.locator("[data-problems-tab='closed']").click()
    page.locator("[data-problem-group]").filter(has_text="resolved").wait_for()

    # The signed-in user's own report, resolved by someone else, shows the note on the dashboard.
    session = browser_server["db_factory"]()
    try:
        other_admin = _extra_user(session, "problem-admin", admin=True)
        own = session.query(ProblemReport).filter(ProblemReport.user_id == seed["user_id"]).one()
        ProblemReportService(session).apply([own.id], "resolve", other_admin, note="Fixed the archive")
        session.get(User, seed["user_id"]).is_superuser = False
        session.commit()
    finally:
        session.close()

    page.goto(f"{browser_server['base_url']}/user/dashboard", wait_until="networkidle")
    card = page.locator("[data-my-problem-reports-card]")
    card.wait_for(state="visible")
    assert "No open reports" in card.locator("[data-my-problem-reports-summary]").inner_text()
    card.get_by_role("link", name="View My Reports").click()
    page.wait_for_url("**/user/problems")

    section = page.locator("[data-my-problem-reports]")
    # With nothing open, the page starts on the Closed tab.
    report = section.locator("[data-my-report]")
    report.wait_for()
    assert "Admin: Fixed the archive" in report.inner_text()
    assert "resolved" in report.inner_text().lower()
    assert ("scan_quality", 2, "Blurry", "resolved", "Replaced with a cleaner scan") in _reports(browser_server)


@pytest.mark.browser
def test_report_buttons_hidden_when_setting_is_off(page, browser_server, problem_reports_state):
    seed = browser_server["seed"]
    session = browser_server["db_factory"]()
    try:
        session.add(SystemSetting(
            key=PROBLEM_REPORTS_ENABLED_SETTING, value="false", category="features", data_type="bool",
        ))
        session.commit()
    finally:
        session.close()

    page.goto(f"{browser_server['base_url']}/comics/{seed['active_comic_id']}", wait_until="networkidle")
    page.get_by_role("button", name="Mark Read").wait_for()
    assert page.locator("[data-report-problem]").count() == 0
    assert page.locator("[data-problem-report-modal]").count() == 0

    page.goto(f"{browser_server['base_url']}/reader/{seed['active_comic_id']}", wait_until="networkidle")
    page.wait_for_selector(".reader-container")
    assert page.locator("[data-report-problem-toggle]").count() == 0
    assert page.locator("[data-reader-toolbar-report-problem]").count() == 0
