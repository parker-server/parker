from fastapi import APIRouter, FastAPI

from app.core.utils import get_route_map


def test_get_route_map_uses_flattened_routes():
    app = FastAPI()

    libraries_router = APIRouter()
    pages_router = APIRouter()
    admin_router = APIRouter()

    @libraries_router.get("/", name="list")
    def list_libraries():
        return []

    @pages_router.get("/login", name="login")
    def login_page():
        return {}

    @admin_router.get("/reports", name="reports", tags=["admin"])
    def admin_reports():
        return {}

    app.include_router(libraries_router, prefix="/api/libraries", tags=["libraries"])
    app.include_router(pages_router, tags=["pages"])
    app.include_router(admin_router, prefix="/admin", tags=["admin"])

    route_map = get_route_map(app)
    admin_route_map = get_route_map(app, with_admin_routes=True)

    assert route_map["libraries"]["list"] == "/api/libraries"
    assert route_map["pages"]["login"] == "/login"
    assert route_map["login"] == "/login"
    assert "admin" not in route_map
    assert admin_route_map["admin"]["reports"] == "/admin/reports"


def test_get_route_map_supports_nested_included_routers():
    home_router = APIRouter()
    discovery_router = APIRouter()

    @discovery_router.get("/random", name="random_gems")
    def random_gems():
        return []

    @discovery_router.get("/parker-rated", name="top_parker_rated")
    def top_parker_rated():
        return []

    @discovery_router.get("/trending", name="trending")
    def trending():
        return []

    home_router.include_router(discovery_router)
    app = FastAPI()
    app.include_router(home_router, prefix="/api/home", tags=["home"])

    route_map = get_route_map(app)

    assert route_map["home"]["random_gems"] == "/api/home/random"
    assert route_map["home"]["top_parker_rated"] == "/api/home/parker-rated"
    assert route_map["home"]["trending"] == "/api/home/trending"


def test_flat_alias_does_not_override_namespace():
    app = FastAPI()

    libraries_router = APIRouter()
    series_router = APIRouter()

    @libraries_router.get("/{library_id}/series", name="series")
    def library_series(library_id: int):
        return []

    @series_router.get("/", name="list")
    def list_series():
        return []

    app.include_router(libraries_router, prefix="/api/libraries", tags=["libraries"])
    app.include_router(series_router, prefix="/api/series", tags=["series"])

    route_map = get_route_map(app)

    assert route_map["series"]["list"] == "/api/series"
    assert route_map["libraries"]["series"] == "/api/libraries/{library_id}/series"
