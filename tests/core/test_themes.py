import re
from pathlib import Path

import pytest

from app.core.themes import ACCENTS, DEFAULT_ACCENT, DEFAULT_THEME, THEMES, resolve_appearance

ROOT = Path(__file__).resolve().parents[2]
THEMES_CSS = ROOT / "static" / "css" / "themes.css"
# Steps base.html maps Tailwind's gray and blue scales onto.
TAILWIND_STEPS = (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950)
THEME_VARIABLES = {
    *(f"gray-{step}" for step in TAILWIND_STEPS),
    "gray-650",
    "gray-750",
    "backdrop-base-top",
    "backdrop-base-bottom",
    "backdrop-overlay",
}
ACCENT_VARIABLES = {f"accent-{step}" for step in TAILWIND_STEPS}
THEME_VARIABLE_USE = re.compile(r"var\(--((?:gray|accent|backdrop)-[a-z0-9-]+)\)")
WHITE = (255, 255, 255)
AA_TEXT = 4.5


def _blocks(attribute: str) -> dict[str, dict[str, tuple[int, int, int]]]:
    """Variables per `[data-<attribute>="name"]` block in themes.css."""
    css = THEMES_CSS.read_text(encoding="utf-8")
    blocks: dict[str, dict[str, tuple[int, int, int]]] = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        values = {
            name: tuple(int(part) for part in value.split())
            for name, value in re.findall(r"--([a-z0-9-]+)\s*:\s*([^;]+);", body)
        }
        for name in re.findall(rf'\[data-{attribute}="([a-z]+)"\]', selectors):
            blocks[name] = values
    return blocks


def _luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = rgb
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def _contrast(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _at_least_as_readable(ratio: float, default_ratio: float) -> bool:
    # A new palette may not be harder to read than the default, unless it still meets WCAG AA.
    return ratio >= min(AA_TEXT, default_ratio) - 0.01


def test_css_has_a_block_for_every_theme_and_accent():
    assert set(_blocks("theme")) == set(THEMES)
    assert set(_blocks("accent")) == set(ACCENTS)


@pytest.mark.parametrize("theme", THEMES)
def test_every_theme_sets_every_variable(theme):
    assert set(_blocks("theme")[theme]) == THEME_VARIABLES


@pytest.mark.parametrize("accent", ACCENTS)
def test_every_accent_sets_every_step(accent):
    assert set(_blocks("accent")[accent]) == ACCENT_VARIABLES


def test_every_theme_variable_used_is_defined():
    defined = THEME_VARIABLES | ACCENT_VARIABLES
    sources = [*(ROOT / "static" / "css").glob("*.css"), *(ROOT / "app" / "templates").rglob("*.html")]
    used: dict[str, set[str]] = {}
    for path in sources:
        for name in THEME_VARIABLE_USE.findall(path.read_text(encoding="utf-8")):
            used.setdefault(name, set()).add(path.relative_to(ROOT).as_posix())

    assert used, "expected stylesheets and templates to use theme variables"
    undefined = {name: sorted(paths) for name, paths in used.items() if name not in defined}
    assert undefined == {}


# Body text, secondary text and muted labels on page, card and input backgrounds.
TEXT_PAIRS = [(text, background) for text in (100, 200, 300, 400) for background in (700, 800, 900, 950)]


@pytest.mark.parametrize("theme", THEMES)
def test_theme_text_is_as_readable_as_default(theme):
    default, palette = _blocks("theme")[DEFAULT_THEME], _blocks("theme")[theme]
    for text, background in TEXT_PAIRS:
        ratio = _contrast(palette[f"gray-{text}"], palette[f"gray-{background}"])
        default_ratio = _contrast(default[f"gray-{text}"], default[f"gray-{background}"])
        assert _at_least_as_readable(ratio, default_ratio), (text, background, round(ratio, 2))


@pytest.mark.parametrize("accent", ACCENTS)
def test_accent_buttons_are_as_readable_as_default(accent):
    # White text on accent buttons: bg-blue-500 / -600 / -700.
    default, palette = _blocks("accent")[DEFAULT_ACCENT], _blocks("accent")[accent]
    for step in (500, 600, 700):
        ratio = _contrast(WHITE, palette[f"accent-{step}"])
        default_ratio = _contrast(WHITE, default[f"accent-{step}"])
        assert _at_least_as_readable(ratio, default_ratio), (step, round(ratio, 2))


@pytest.mark.parametrize("accent", ACCENTS)
@pytest.mark.parametrize("theme", THEMES)
def test_accent_text_is_as_readable_as_default_on_every_theme(theme, accent):
    # Accent text (text-blue-300 / -400) on cards and the page.
    default_gray, default_accent = _blocks("theme")[DEFAULT_THEME], _blocks("accent")[DEFAULT_ACCENT]
    gray, palette = _blocks("theme")[theme], _blocks("accent")[accent]
    for step in (300, 400):
        for background in (800, 900):
            ratio = _contrast(palette[f"accent-{step}"], gray[f"gray-{background}"])
            default_ratio = _contrast(default_accent[f"accent-{step}"], default_gray[f"gray-{background}"])
            assert _at_least_as_readable(ratio, default_ratio), (step, background, round(ratio, 2))


def test_resolve_appearance_prefers_the_users_pick():
    resolved = resolve_appearance(
        {"theme": "noir", "accent": "red"},
        user_themes_enabled=True,
        default_theme="midnight",
        default_accent="violet",
    )

    assert resolved == {"theme": "noir", "accent": "red"}


def test_resolve_appearance_falls_back_to_server_defaults():
    partial = resolve_appearance(
        {"accent": "red"}, user_themes_enabled=True, default_theme="midnight", default_accent="violet"
    )
    turned_off = resolve_appearance(
        {"theme": "noir", "accent": "red"}, user_themes_enabled=False, default_theme="midnight", default_accent="violet"
    )

    assert partial == {"theme": "midnight", "accent": "red"}
    assert turned_off == {"theme": "midnight", "accent": "violet"}


def test_resolve_appearance_ignores_unknown_names():
    resolved = resolve_appearance(
        {"theme": "bogus", "accent": "plaid"}, user_themes_enabled=True, default_theme=None, default_accent="nope"
    )

    assert resolved == {"theme": DEFAULT_THEME, "accent": DEFAULT_ACCENT}
