import pytest


def _color(page, selector: str, prop: str) -> str:
    return page.eval_on_selector(selector, f"el => getComputedStyle(el).{prop}")


@pytest.mark.browser
def test_tailwind_gray_and_blue_follow_theme_variables(page, browser_server):
    page.goto(f"{browser_server['base_url']}/insights", wait_until="networkidle")
    eyebrow = "p.text-blue-300"
    card = "a[href$='/timelines']"
    creator_link = "a[href$='/insights/creator-chemistry'] .text-blue-fixed-300"

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
    # Creator chemistry keeps its own blue whatever the accent.
    assert _color(page, creator_link, "color") == "rgb(147, 197, 253)"
