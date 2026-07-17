from fastapi.routing import iter_route_contexts


def _normalize_route_key(value: str) -> str:
    return value.replace("-", "_")


def _normalize_route_path(path: str | None) -> str:
    if not path:
        return "/"
    if path != "/":
        return path.rstrip("/")
    return path


def _get_route_namespace(route, tags: list[str], route_path: str) -> str:
    non_admin_tags = [tag for tag in tags if tag != "admin"]
    if non_admin_tags:
        return non_admin_tags[0]

    path_parts = [part for part in route_path.split("/") if part and not part.startswith("{")]
    if path_parts:
        if path_parts[0] == "api" and len(path_parts) > 1:
            return _normalize_route_key(path_parts[1])
        if path_parts[0] == "admin":
            return "admin"
        if path_parts[0] == "opds":
            return "opds"
        if len(path_parts) == 1 and path_parts[0] == "health":
            return "health"

    module_name = getattr(route.endpoint, "__module__", "") or ""
    module_leaf = module_name.rsplit(".", 1)[-1]
    if module_leaf:
        return _normalize_route_key(module_leaf)

    return "misc"


def get_route_map(app, with_admin_routes: bool = False):
    """
    Generates a route lookup map for the frontend.
    """
    route_map = {}
    flat_candidates = {}

    for route_context in iter_route_contexts(app.routes):
        if not route_context.name:
            continue

        normalized_tags = [
            _normalize_route_key(tag) for tag in (getattr(route_context, "tags", []) or []) if tag
        ]
        if not with_admin_routes and "admin" in normalized_tags:
            continue

        route_name = _normalize_route_key(route_context.name)
        route_path = _normalize_route_path(route_context.path)
        namespace = _get_route_namespace(route_context, normalized_tags, route_path)

        route_map.setdefault(namespace, {})[route_name] = route_path

        if route_name not in route_map:
            flat_candidates.setdefault(route_name, set()).add(route_path)

    for route_name, paths in flat_candidates.items():
        if len(paths) == 1 and route_name not in route_map:
            route_map[route_name] = next(iter(paths))

    return route_map
