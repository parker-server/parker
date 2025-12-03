from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse

from app.core.templates import templates

router = APIRouter()


# Frontend routes
@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Home page - Library browser"""
    return templates.TemplateResponse("index.html", {"request": request})

@router.get("/library/{library_id}", response_class=HTMLResponse)
async def library_view(request: Request, library_id: int):
    """View a specific library"""
    return templates.TemplateResponse("library.html", {
        "request": request,
        "library_id": library_id
    })

@router.get("/series/{series_id}", response_class=HTMLResponse)
async def series_detail(request: Request, series_id: int):
    """Series detail page"""
    return templates.TemplateResponse("comics/series_detail.html", {
        "request": request,
        "series_id": series_id
    })

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

@router.get("/volumes/{volume_id}", response_class=HTMLResponse)
async def volume_detail(request: Request, volume_id: int):
    """Volume detail view"""
    return templates.TemplateResponse("comics/volume_detail.html", {
        "request": request,
        "volume_id": volume_id
    })

@router.get("/comics/{comic_id}", response_class=HTMLResponse)
async def comic_detail(request: Request, comic_id: int):
    """Comic metadata detail page"""
    return templates.TemplateResponse("comics/comic_detail.html", {
        "request": request,
        "comic_id": comic_id
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