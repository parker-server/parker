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
