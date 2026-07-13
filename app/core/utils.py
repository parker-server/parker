from fastapi.routing import APIRoute


def _normalize_route_key(value: str) -> str:
    return value.replace("-", "_")


def _join_paths(prefix: str, path: str) -> str:
    if not prefix:
        return path or "/"
    if not path or path == "/":
        return prefix
    return f"{prefix.rstrip('/')}/{path.lstrip('/')}"


def _iter_api_routes(routes, inherited_prefix: str = "", inherited_tags=None):
    inherited_tags = list(inherited_tags or [])

    for route in routes:
        if isinstance(route, APIRoute):
            yield route, inherited_prefix, inherited_tags + list(getattr(route, "tags", []) or [])
            continue

        original_router = getattr(route, "original_router", None)
        include_context = getattr(route, "include_context", None)
        if original_router is None or include_context is None:
            continue

        child_prefix = _join_paths(inherited_prefix, getattr(include_context, "prefix", "") or "")
        child_tags = inherited_tags + list(getattr(include_context, "tags", []) or [])

        yield from _iter_api_routes(original_router.routes, child_prefix, child_tags)


def _get_route_namespace(route: APIRoute, tags: list[str], route_path: str) -> str:
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

    for route, prefix, tags in _iter_api_routes(app.routes):
        if not route.name:
            continue

        normalized_tags = [_normalize_route_key(tag) for tag in tags if tag]
        if not with_admin_routes and "admin" in normalized_tags:
            continue

        route_name = _normalize_route_key(route.name)
        route_path = _join_paths(prefix, route.path)
        namespace = _get_route_namespace(route, normalized_tags, route_path)

        route_map.setdefault(namespace, {})[route_name] = route_path

        if route_name not in route_map:
            flat_candidates.setdefault(route_name, set()).add(route_path)

    for route_name, paths in flat_candidates.items():
        if len(paths) == 1 and route_name not in route_map:
            route_map[route_name] = next(iter(paths))

    return route_map
