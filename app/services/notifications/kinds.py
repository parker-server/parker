"""
The notification kind registry.

Each kind is declared once here. The service uses it to render text and links,
and the API, bell and preferences page use it for labels and icons. Adding a
kind means adding an entry here and a producer that calls notify().
"""
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.services.settings_service import PROBLEM_REPORTS_ENABLED_SETTING


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
    # A bool setting that turns the kind's feature on and off. While it is off the
    # kind is not sent and its switch is hidden from preferences. A missing
    # setting counts as on.
    feature_setting: Optional[str] = None


def _render_whats_new(count: int, data: dict[str, Any]) -> tuple[str, Optional[str]]:
    title = f"What's new in Parker {data['version']}"
    first_title = data.get("first_title")
    if not first_title:
        return title, None
    if count <= 1:
        return title, first_title
    return title, f"{first_title}, and {count - 1} more"


def _render_follow_arrivals(count: int, data: dict[str, Any]) -> tuple[str, Optional[str]]:
    volume_name = f"{data['series_name']} Vol {data['volume_number']}"
    return f"{count} new issue{'s' if count != 1 else ''} in {volume_name}", None


def _render_year_in_review(count: int, data: dict[str, Any]) -> tuple[str, Optional[str]]:
    year = data["year"]
    return (
        f"Your {year} Year in Review is ready",
        f"See your favorite series and reading milestones from {year}.",
    )


def _render_update_available(count: int, data: dict[str, Any]) -> tuple[str, Optional[str]]:
    return (
        f"Parker {data['version']} is available",
        f"This server is running {data['current_version']}.",
    )


def _render_problem_resolved(count: int, data: dict[str, Any]) -> tuple[str, Optional[str]]:
    if count > 1:
        return f"{count} of your problem reports were closed", None
    outcome = "resolved" if data.get("status") == "resolved" else "dismissed"
    return f"Your report on {data['comic_name']} was {outcome}", data.get("note") or None


def _render_admin_problem_new(count: int, data: dict[str, Any]) -> tuple[str, Optional[str]]:
    latest = f"{data['category_label']} in {data['comic_name']}"
    if count > 1:
        return f"{count} new problem reports", f"Latest: {latest}"
    return f"New problem report: {latest}", None


KINDS: dict[str, NotificationKind] = {
    kind.key: kind
    for kind in (
        NotificationKind(
            key="follow_arrivals",
            label="New issues in followed volumes",
            description="When a scan adds issues to a volume you follow.",
            icon="book-open",
            render=_render_follow_arrivals,
            link=lambda data: f"/volumes/{data['volume_id']}",
        ),
        NotificationKind(
            key="year_in_review",
            label="Year in Review",
            description="When your Year in Review is ready in December.",
            icon="party-popper",
            render=_render_year_in_review,
            link=lambda data: f"/user/year-in-review?year={data['year']}",
        ),
        NotificationKind(
            key="whats_new",
            label="What's new in Parker",
            description="New features after Parker is updated.",
            icon="sparkles",
            render=_render_whats_new,
            link=lambda data: "/whats-new",
        ),
        NotificationKind(
            key="problem_resolved",
            label="Problem reports",
            description="When an admin resolves or dismisses a problem you reported.",
            icon="circle-check",
            render=_render_problem_resolved,
            link=lambda data: "/user/problems",
            feature_setting=PROBLEM_REPORTS_ENABLED_SETTING,
        ),
        NotificationKind(
            key="admin_problem_new",
            label="New problem reports",
            description="When a reader reports a problem with a comic.",
            icon="triangle-alert",
            render=_render_admin_problem_new,
            link=lambda data: "/admin/problems",
            admin_only=True,
            feature_setting=PROBLEM_REPORTS_ENABLED_SETTING,
        ),
        NotificationKind(
            key="admin_update_available",
            label="Parker updates",
            description="When a newer Parker release is available.",
            icon="download",
            render=_render_update_available,
            link=lambda data: "/admin/about",
            admin_only=True,
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
