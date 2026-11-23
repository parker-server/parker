"""
Site themes and accent colors users can pick.

Each name here has a matching `[data-theme]` / `[data-accent]` block in
static/css/themes.css; a test keeps the two in step.
"""

from typing import Any, Optional

THEMES: dict[str, str] = {
    "default": "Default",
    "midnight": "Midnight",
    "noir": "Noir",
    "newsprint": "Newsprint",
}
ACCENTS: dict[str, str] = {
    "blue": "Blue",
    "red": "Red",
    "amber": "Amber",
    "emerald": "Emerald",
    "violet": "Violet",
}
DEFAULT_THEME = "default"
DEFAULT_ACCENT = "blue"


def resolve_appearance(
    appearance: Optional[dict[str, Any]],
    *,
    user_themes_enabled: bool,
    default_theme: Optional[str],
    default_accent: Optional[str],
) -> dict[str, str]:
    """The theme and accent to show: the user's pick when allowed, else the server default."""
    theme = default_theme if default_theme in THEMES else DEFAULT_THEME
    accent = default_accent if default_accent in ACCENTS else DEFAULT_ACCENT
    if user_themes_enabled and appearance:
        if appearance.get("theme") in THEMES:
            theme = appearance["theme"]
        if appearance.get("accent") in ACCENTS:
            accent = appearance["accent"]
    return {"theme": theme, "accent": accent}
