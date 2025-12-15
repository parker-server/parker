from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.comic_helpers import get_smart_cover
from app.api.deps import SessionDep, ComicDep, VolumeDep, SeriesDep, LibraryDep, CurrentUser
from app.core.templates import templates
from app.models.comic import Comic, Volume
from app.core.login_effects import get_active_effect

router = APIRouter()


# Frontend routes
@router.get("/", response_class=HTMLResponse, name="home")
async def home(request: Request, user: CurrentUser):
    """Home page - Library browser"""
    return templates.TemplateResponse(request=request, name="index.html")


@router.get("/reader/{comic_id}", response_class=HTMLResponse, name="reader")
async def reader(request: Request, comic_id: int, user: CurrentUser):
    """Comic reader"""
    return templates.TemplateResponse(request=request, name="reader.html", context={
        "comic_id": comic_id
    })

@router.get("/search", response_class=HTMLResponse, name="search")
async def search(request: Request, user: CurrentUser):
    """Search page"""
    return templates.TemplateResponse(request=request, name="search.html")

@router.get("/collections", response_class=HTMLResponse, name="collections")
async def collections_view(request: Request, user: CurrentUser):
    """Collections page"""
    return templates.TemplateResponse(request=request, name="collections/collections.html")

@router.get("/collections/{collection_id}", response_class=HTMLResponse, name="collection_detail")
async def collection_detail(request: Request, collection_id: int, user: CurrentUser):
    return templates.TemplateResponse(request=request, name="collections/collection_detail.html",
                                      context={"collection_id": collection_id})

@router.get("/reading-lists", response_class=HTMLResponse, name="reading_lists")
async def reading_lists_view(request: Request, user: CurrentUser):
    """Reading lists page"""
    return templates.TemplateResponse(request=request, name="reading_lists/reading_lists.html")

@router.get("/reading-lists/{reading_list_id}", response_class=HTMLResponse, name="reading_list_detail")
async def reading_list_detail(request: Request, reading_list_id: int, user: CurrentUser):
    return templates.TemplateResponse(request=request, name="reading_lists/reading_list_detail.html", context={
        "reading_list_id": reading_list_id
    })

@router.get("/continue-reading", response_class=HTMLResponse, name="continue_reading")
async def continue_reading(request: Request, user: CurrentUser):
    """Continue reading page"""
    return templates.TemplateResponse(request=request, name="continue_reading.html")

@router.get("/libraries", response_class=HTMLResponse, name="libraries")
async def libraries_page(request: Request):
    return templates.TemplateResponse("libraries/index.html", {"request": request})

@router.get("/libraries/{library_id}", response_class=HTMLResponse, name="library_detail")
async def library_view(request: Request, library: LibraryDep, user: CurrentUser):
    """View a specific library"""
    return templates.TemplateResponse(request=request, name="libraries/library.html", context={
        "library_id": library.id
    })

@router.get("/series/{series_id}", response_class=HTMLResponse, name="series_detail")
async def series_detail(request: Request, series: SeriesDep, db: SessionDep, user: CurrentUser):
    """Series detail page"""

    # Find the "Smart Cover" to use for colors/background
    base_query = db.query(Comic).join(Volume).filter(Volume.series_id == series.id)
    cover_comic = get_smart_cover(base_query, series.name)

    return templates.TemplateResponse(request=request, name="comics/series_detail.html", context={
        "series_id": series.id,
        "cover": cover_comic
    })


@router.get("/volumes/{volume_id}", response_class=HTMLResponse, name="volume_detail")
async def volume_detail(request: Request, volume: VolumeDep, db: SessionDep, user: CurrentUser):
    """Volume detail view"""

    # Find the "Smart Cover" to use for colors/background
    base_query = db.query(Comic).join(Volume).filter(Volume.id == volume.id)
    cover_comic = get_smart_cover(base_query, volume.series.name)

    return templates.TemplateResponse(request=request, name="comics/volume_detail.html", context={
        "volume_id": volume.id,
        "cover": cover_comic
    })

@router.get("/comics/{comic_id}", response_class=HTMLResponse, name="comic_detail")
async def comic_detail(request: Request, comic: ComicDep, user: CurrentUser):
    """
    Comic detail page.
    Fetches basic metadata server-side for Open Graph tags and Hero Backgrounds.
    """

    return templates.TemplateResponse(request=request, name="comics/comic_detail.html", context={
        "comic_id": comic.id,
        "comic": comic
    })

@router.get("/login", response_class=HTMLResponse, name="login")
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login_full.html", context={
        "active_effect": get_active_effect()
    })

@router.get("/pull-lists", response_class=HTMLResponse, name="pull_lists")
async def pull_lists_index(request: Request, user: CurrentUser):
    return templates.TemplateResponse(request=request, name="pull_lists/index.html")

@router.get("/pull-lists/{list_id}", response_class=HTMLResponse, name="pull_list_detail")
async def pull_list_detail(request: Request, list_id: int, user: CurrentUser):
    # We pass the ID to the template; Alpine handles the data fetching
    return templates.TemplateResponse(request=request, name="pull_lists/detail.html", context={
        "list_id": list_id
    })

@router.get("/user/dashboard", response_class=HTMLResponse, name="user_dashboard")
async def dashboard(request: Request, user: CurrentUser):
    return templates.TemplateResponse(request=request, name="user/dashboard.html")

@router.get("/user/settings", response_class=HTMLResponse, name="user_settings")
async def settings_page(request: Request, user: CurrentUser):
    return templates.TemplateResponse("user/settings.html", {"request": request})

@router.get("/browse/{context_type}/{context_id}", response_class=HTMLResponse, name="cover_browser")
async def cover_browser_page(request: Request, context_type: str, context_id: int, user: CurrentUser):
    # Pass label logic or let JS fetch it
    return templates.TemplateResponse(request=request, name="comics/cover_browser.html", context={
        "context_type": context_type,
        "context_id": context_id,
        "context_label": context_type.title() # Simple default
    })

@router.get("/404", response_class=HTMLResponse, name="404")
async def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="status_codes/404.html")

