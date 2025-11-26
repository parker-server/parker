from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# Setup templates
# Note: Path is relative to where you run the command, usually root
templates = Jinja2Templates(directory="app/templates")

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
    return templates.TemplateResponse("series_detail.html", {
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
    return templates.TemplateResponse("collections.html", {"request": request})

@router.get("/collections/{collection_id}", response_class=HTMLResponse)
async def collection_detail(request: Request, collection_id: int):
    return templates.TemplateResponse("collection_detail.html", {
        "request": request,
        "collection_id": collection_id
    })

@router.get("/reading-lists", response_class=HTMLResponse)
async def reading_lists_view(request: Request):
    """Reading lists page"""
    return templates.TemplateResponse("reading_lists.html", {"request": request})

@router.get("/reading-lists/{reading_list_id}", response_class=HTMLResponse)
async def reading_list_detail(request: Request, reading_list_id: int):
    return templates.TemplateResponse("reading_list_detail.html", {
        "request": request,
        "reading_list_id": reading_list_id
    })

@router.get("/continue-reading", response_class=HTMLResponse)
async def continue_reading(request: Request):
    """Continue reading page"""
    return templates.TemplateResponse("continue_reading.html", {"request": request})

@router.get("/comics/{comic_id}", response_class=HTMLResponse)
async def comic_detail(request: Request, comic_id: int):
    """Comic metadata detail page"""
    return templates.TemplateResponse("comic_detail.html", {
        "request": request,
        "comic_id": comic_id
    })