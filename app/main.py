from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import HTTPException
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from app.database import engine, Base
from app.config import settings

# IMPORTANT: Import all models here so SQLAlchemy knows about them
from app.models.library import Library
from app.models.series import Series
from app.models.comic import Volume, Comic
from app.models.tags import Character, Team, Location
from app.models.credits import Person, ComicCredit
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.collection import Collection, CollectionItem
from app.models.reading_list import ReadingList, ReadingListItem

# API Routes
from app.api import libraries, comics, reader, reading_lists, collections, progress, series, volumes


# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print(f"Starting {settings.app_name}")

    # Ensure directories exist
    Path("storage/database").mkdir(parents=True, exist_ok=True)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    settings.cover_dir.mkdir(parents=True, exist_ok=True)

    # Create database tables (models are now imported)
    Base.metadata.create_all(bind=engine)

    logger.info("Comic Server starting up...")
    logger.info("Frontend available at http://localhost:8000")
    logger.info("API docs available at http://localhost:8000/docs")

    yield
    # Shutdown
    logger.info("Comic Server shutting down...")
    print("Shutting down")


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# CORS middleware (adjust origins as needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")


# Global exception handler
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if "text/html" in request.headers.get("accept", ""):
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "status_code": exc.status_code, "detail": exc.detail},
            status_code=exc.status_code
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail}
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unexpected error: {exc}", exc_info=True)
    if "text/html" in request.headers.get("accept", ""):
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "status_code": 500, "detail": "Internal server error"},
            status_code=500
        )
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"}
    )


# Include routers
app.include_router(libraries.router, prefix="/api/libraries", tags=["libraries"])
app.include_router(series.router, prefix="/api/series", tags=["series"])
app.include_router(volumes.router, prefix="/api/volumes", tags=["volumes"])
app.include_router(comics.router, prefix="/api/comics", tags=["comics"])
app.include_router(reader.router, prefix="/api/reader", tags=["reader"])
app.include_router(reading_lists.router, prefix="/api/reading-lists", tags=["reading-lists"])
app.include_router(collections.router, prefix="/api/collections", tags=["collections"])
app.include_router(progress.router, prefix="/api/progress", tags=["progress"])


# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "comic-server"}


# Frontend routes
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Home page - Library browser"""
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/library/{library_id}", response_class=HTMLResponse)
async def library_view(request: Request, library_id: int):
    """View a specific library"""
    return templates.TemplateResponse("library.html", {
        "request": request,
        "library_id": library_id
    })

@app.get("/reader/{comic_id}", response_class=HTMLResponse)
async def reader(request: Request, comic_id: int):
    """Comic reader"""
    return templates.TemplateResponse("reader.html", {
        "request": request,
        "comic_id": comic_id
    })

@app.get("/search", response_class=HTMLResponse)
async def search(request: Request):
    """Search page"""
    return templates.TemplateResponse("search.html", {"request": request})

@app.get("/collections", response_class=HTMLResponse)
async def collections_view(request: Request):
    """Collections page"""
    return templates.TemplateResponse("collections.html", {"request": request})

@app.get("/collections/{collection_id}", response_class=HTMLResponse)
async def collection_detail(request: Request, collection_id: int):
    return templates.TemplateResponse("collection_detail.html", {
        "request": request,
        "collection_id": collection_id
    })

@app.get("/reading-lists", response_class=HTMLResponse)
async def reading_lists_view(request: Request):
    """Reading lists page"""
    return templates.TemplateResponse("reading_lists.html", {"request": request})

@app.get("/reading-lists/{reading_list_id}", response_class=HTMLResponse)
async def reading_list_detail(request: Request, reading_list_id: int):
    return templates.TemplateResponse("reading_list_detail.html", {
        "request": request,
        "reading_list_id": reading_list_id
    })


@app.get("/continue-reading", response_class=HTMLResponse)
async def continue_reading(request: Request):
    """Continue reading page"""
    return templates.TemplateResponse("continue_reading.html", {"request": request})
