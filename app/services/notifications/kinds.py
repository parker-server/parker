"""
The notification kind registry.

Each kind is declared once here. The service uses it to render text and links,
and the API, bell and preferences page use it for labels and icons. Adding a
kind means adding an entry here and a producer that calls notify().
"""
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class NotificationKind:
    key: str
    label: str
    description: str
    # Name of an icon in partials/lucide_icon.html
    icon: str
    # (count, data) -> (title, body)
    render: Callable[[int, dict[str, Any]], tuple[str, Optional[str]]]
    # data -> app-relative path, or None
    link: Callable[[dict[str, Any]], Optional[str]]
    admin_only: bool = False
    default_enabled: bool = True


def _render_whats_new(count: int, data: dict[str, Any]) -> tuple[str, Optional[str]]:
    title = f"What's new in Parker {data['version']}"
    first_title = data.get("first_title")
    if not first_title:
        return title, None
    if count <= 1:
        return title, first_title
    return title, f"{first_title}, and {count - 1} more"


KINDS: dict[str, NotificationKind] = {
    kind.key: kind
    for kind in (
        NotificationKind(
            key="whats_new",
            label="What's new in Parker",
            description="New features after Parker is updated.",
            icon="sparkles",
            render=_render_whats_new,
            link=lambda data: "/whats-new",
        ),
    )
}

# Icon shown for rows whose kind has since been removed from the registry.
FALLBACK_ICON = "info"


def get_kind(key: str) -> NotificationKind:
    kind = KINDS.get(key)
    if kind is None:
        raise ValueError(f"Unknown notification kind: {key}")
    return kind
