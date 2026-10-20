import pytest
from jinja2 import TemplateRuntimeError

from app.core.templates import templates


def test_template_error_raises_with_message_when_rendered():
    template = templates.env.from_string(
        '{% if a and b %}{{ template_error("pass a or b, not both") }}{% endif %}'
    )

    with pytest.raises(TemplateRuntimeError, match="pass a or b, not both"):
        template.render(a=True, b=True)


def test_template_error_is_not_raised_when_branch_is_not_rendered():
    template = templates.env.from_string(
        '{% if a and b %}{{ template_error("pass a or b, not both") }}{% endif %}ok'
    )

    assert template.render(a=True, b=False) == "ok"


def test_template_error_is_available_inside_imported_macros():
    macro_source = (
        '{% macro pick(a=None, b=None) %}'
        '{% if a and b %}{{ template_error("pick(): pass a or b, not both") }}{% endif %}'
        '{{ a or b }}'
        '{% endmacro %}'
    )
    module = templates.env.from_string(macro_source).module

    assert str(module.pick(a="x")) == "x"
    with pytest.raises(TemplateRuntimeError, match="pick\\(\\): pass a or b, not both"):
        module.pick(a="x", b="y")


def _render_rail(args: str) -> str:
    return templates.env.from_string(
        '{% from "partials/rail.html" import rail %}'
        '{% call rail(' + args + ') %}<i>items</i>{% endcall %}'
    ).render()


def test_rail_renders_static_title_with_icon_and_default_scroller():
    html = _render_rail('title="Trending", icon="zap", icon_class="text-amber-300"')

    assert "Trending" in html
    assert 'data-lucide-icon="zap"' in html
    assert "h-5 w-5 flex-shrink-0 text-amber-300" in html
    assert 'class="flex gap-4 overflow-x-auto pb-6 scrollbar-thin' in html
    assert "<i>items</i>" in html
    assert "<a " not in html
    assert "<button" not in html


def test_rail_binds_title_expr_on_inner_span_so_icon_is_kept():
    html = _render_rail('title_expr="rail.title", icon="sparkles"')

    assert '<span x-text="rail.title"></span>' in html
    assert 'data-lucide-icon="sparkles"' in html
    assert '<h2 class="text-xl font-bold text-white flex items-center gap-2" x-text' not in html


def test_rail_renders_link_action_with_default_class():
    html = _render_rail('title="New", action_label="View All", action_href_expr="viewAllUrl()"')

    assert '<a :href="viewAllUrl()" class="text-xs text-blue-400 hover:text-white transition-colors">View All</a>' in html
    assert "<button" not in html


def test_rail_renders_button_action():
    html = _render_rail('title="Random", action_label="Spin Again", action_click="fetchRandom(false)"')

    assert '<button type="button" x-on:click="fetchRandom(false)"' in html
    assert ">Spin Again</button>" in html
    assert "<a " not in html


def test_rail_class_overrides_replace_defaults():
    html = _render_rail(
        'title="Recent", action_label="View All", action_href_expr="url()", '
        'action_class="text-sm text-blue-400", scroller_class="flex gap-6 pb-4"'
    )

    assert 'class="text-sm text-blue-400"' in html
    assert 'class="flex gap-6 pb-4"' in html
    assert "text-xs" not in html
    assert "gap-4" not in html


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ('title="A", title_expr="b"', "exactly one of title or title_expr"),
        ('', "exactly one of title or title_expr"),
        ('title="A", action_label="Go", action_href_expr="u()", action_click="c()"', "not both"),
        ('title="A", action_href_expr="u()"', "an action requires action_label"),
        ('title="A", action_click="c()"', "an action requires action_label"),
        ('title="A", action_label="Go"', "action_label requires action_href_expr or action_click"),
    ],
)
def test_rail_rejects_invalid_arguments(args, message):
    with pytest.raises(TemplateRuntimeError, match=message):
        _render_rail(args)
