from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse

from app.api.deps import SessionDep, ComicDep, VolumeDep, SeriesDep, LibraryDep
from app.core.templates import templates
from app.models.comic import Comic

router = APIRouter()


# Frontend routes
@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Home page - Library browser"""
    return templates.TemplateResponse("index.html", {"request": request})



@router.get("/reader/{comic_id}", response_class=HTMLResponse)
async def reader(request: Request, comic_id: int):
    """Comic reader"""
    return templates.TemplateResponse("reader.html", {
        "request": request,
        "comic_id": comic_id
    })

@router.get("/search", response_class=HTMLResponse)
async def search(request: Request):
    """Search page"""
    return templates.TemplateResponse("search.html", {"request": request})

@router.get("/collections", response_class=HTMLResponse)
async def collections_view(request: Request):
    """Collections page"""
    return templates.TemplateResponse("collections/collections.html", {"request": request})

@router.get("/collections/{collection_id}", response_class=HTMLResponse)
async def collection_detail(request: Request, collection_id: int):
    return templates.TemplateResponse("collections/collection_detail.html", {
        "request": request,
        "collection_id": collection_id
    })

@router.get("/reading-lists", response_class=HTMLResponse)
async def reading_lists_view(request: Request):
    """Reading lists page"""
    return templates.TemplateResponse("reading_lists/reading_lists.html", {"request": request})

@router.get("/reading-lists/{reading_list_id}", response_class=HTMLResponse)
async def reading_list_detail(request: Request, reading_list_id: int):
    return templates.TemplateResponse("reading_lists/reading_list_detail.html", {
        "request": request,
        "reading_list_id": reading_list_id
    })

@router.get("/continue-reading", response_class=HTMLResponse)
async def continue_reading(request: Request):
    """Continue reading page"""
    return templates.TemplateResponse("continue_reading.html", {"request": request})

@router.get("/library/{library_id}", response_class=HTMLResponse)
async def library_view(request: Request, library: LibraryDep):
    """View a specific library"""
    return templates.TemplateResponse("library.html", {
        "request": request,
        "library_id": library.id
    })

@router.get("/series/{series_id}", response_class=HTMLResponse)
async def series_detail(request: Request, series: SeriesDep):
    """Series detail page"""
    return templates.TemplateResponse("comics/series_detail.html", {
        "request": request,
        "series_id": series.id
    })


@router.get("/volumes/{volume_id}", response_class=HTMLResponse)
async def volume_detail(request: Request, volume: VolumeDep):
    """Volume detail view"""
    return templates.TemplateResponse("comics/volume_detail.html", {
        "request": request,
        "volume_id": volume.id
    })

@router.get("/comics/{comic_id}", response_class=HTMLResponse)
async def comic_detail(request: Request, comic: ComicDep):
    """
    Comic detail page.
    Fetches basic metadata server-side for Open Graph tags and Hero Backgrounds.
    """
    # Fetch just what we need for the template shell
    # (Alpine will fetch the heavy stuff like credits/suggestions later)
    #comic = db.query(Comic).filter(Comic.id == comic_id).first()

    #if not comic:
        # Graceful 404 page (or redirect)
    #    return templates.TemplateResponse("404.html", {"request": request}, status_code=404)

    return templates.TemplateResponse("comics/comic_detail.html", {
        "request": request,
        "comic_id": comic.id,
        "comic": comic
    })

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login_full.html", {  # Point to new template
        "request": request,
    })

@router.get("/pull-lists", response_class=HTMLResponse)
async def pull_lists_index(request: Request):
    return templates.TemplateResponse("pull_lists/index.html", {"request": request})

@router.get("/pull-lists/{list_id}", response_class=HTMLResponse)
async def pull_list_detail(request: Request, list_id: int):
    # We pass the ID to the template; Alpine handles the data fetching
    return templates.TemplateResponse("pull_lists/detail.html", {
        "request": request,
        "list_id": list_id
    })

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("user/dashboard.html", {
        "request": request,
    })

@router.get("/browse/{context_type}/{context_id}", response_class=HTMLResponse)
async def cover_browser_page(request: Request, context_type: str, context_id: int):
    # Pass label logic or let JS fetch it
    return templates.TemplateResponse("comics/cover_browser.html", {
        "request": request,
        "context_type": context_type,
        "context_id": context_id,
        "context_label": context_type.title() # Simple default
    })

@router.get("/404", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("status_codes/404.html", {
        "request": request,
    })

