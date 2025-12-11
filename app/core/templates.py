import re
from datetime import datetime
from fastapi.templating import Jinja2Templates
from app.config import settings
from app.core.settings_loader import get_cached_setting
from app.core.utils import get_route_map

templates = Jinja2Templates(directory="app/templates")

def route_map_injector(request):

    # Get tags for current route
    route = request.scope.get("route")
    tags = getattr(route, "tags", [])
    include_admin = "admin" in tags

    return get_route_map(request.app, with_admin_routes=include_admin)


# URL Helper for Jinja
def url_builder(path: str) -> str:
    """
    Jinja helper to prefix paths with BASE_URL.
    Usage: {{ url('/static/css/style.css') }}
    """
    base = settings.clean_base_url
    clean_path = path.lstrip("/")
    return f"{base}/{clean_path}" if base else f"/{clean_path}"


templates.env.globals["app_version"] = settings.version
templates.env.globals["app_name"] = settings.app_name

# Inject URL data into Templates
templates.env.globals["url"] = url_builder
templates.env.globals["base_url"] = settings.clean_base_url
templates.env.globals["routes"] = route_map_injector
templates.env.globals["get_system_setting"] = get_cached_setting

# --- Filters ---
def slugify(value: str) -> str:
    """Convert text to a URL-friendly slug."""
    return re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')


def format_date(value: datetime, fmt: str = "%Y-%m-%d") -> str:
    """Format a datetime object with a given format string."""
    return value.strftime(fmt) if isinstance(value, datetime) else value


def truncate(value: str, length: int = 50) -> str:
    """Truncate text to a certain length with ellipsis."""
    return value[:length] + "…" if len(value) > length else value


def pluralize(count: int, singular: str, plural: str = None) -> str:
    if count == 1:
        return singular
    return plural if plural else singular + "s"


def humanize_number(value: int) -> str:
    return f"{value:,}"  # adds commas


def format_date(value, fmt="%B %d, %Y"):
    from datetime import datetime
    return value.strftime(fmt) if isinstance(value, datetime) else value


# Register filters
templates.env.filters["slugify"] = slugify
templates.env.filters["format_date"] = format_date
templates.env.filters["truncate"] = truncate
templates.env.filters["pluralize"] = pluralize
templates.env.filters["humanize_number"] = humanize_number
templates.env.filters["format_date"] = format_date
