import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
THEMES_CSS = ROOT / "static" / "css" / "themes.css"
# Steps base.html maps Tailwind's gray and blue scales onto.
TAILWIND_STEPS = (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950)
THEME_VARIABLE = re.compile(r"var\(--((?:gray|accent|backdrop)-[a-z0-9-]+)\)")


def _defined_variables() -> set[str]:
    return set(re.findall(r"--([a-z0-9-]+)\s*:", THEMES_CSS.read_text(encoding="utf-8")))


def test_themes_define_every_tailwind_step():
    defined = _defined_variables()
    for prefix in ("gray", "accent"):
        missing = [step for step in TAILWIND_STEPS if f"{prefix}-{step}" not in defined]
        assert missing == [], prefix


def test_every_theme_variable_used_is_defined():
    defined = _defined_variables()
    sources = [*(ROOT / "static" / "css").glob("*.css"), *(ROOT / "app" / "templates").rglob("*.html")]
    used: dict[str, set[str]] = {}
    for path in sources:
        for name in THEME_VARIABLE.findall(path.read_text(encoding="utf-8")):
            used.setdefault(name, set()).add(path.relative_to(ROOT).as_posix())

    assert used, "expected stylesheets and templates to use theme variables"
    undefined = {name: sorted(paths) for name, paths in used.items() if name not in defined}
    assert undefined == {}
