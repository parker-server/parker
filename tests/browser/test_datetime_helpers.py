import pytest


@pytest.mark.browser
def test_browser_datetime_helper_treats_naive_api_timestamps_as_utc(page, browser_server):
    page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")

    parsed = page.evaluate(
        """() => ({
            naive: window.parker.parseUtcDate('2026-08-01T20:15:30').toISOString(),
            spaced: window.parker.parseUtcDate('2026-08-01 20:15:30').toISOString(),
            explicitUtc: window.parker.parseUtcDate('2026-08-01T20:15:30Z').toISOString(),
            offset: window.parker.parseUtcDate('2026-08-01T16:15:30-04:00').toISOString(),
        })"""
    )

    assert parsed == {
        "naive": "2026-08-01T20:15:30.000Z",
        "spaced": "2026-08-01T20:15:30.000Z",
        "explicitUtc": "2026-08-01T20:15:30.000Z",
        "offset": "2026-08-01T20:15:30.000Z",
    }
