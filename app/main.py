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
from app.database import SessionLocal
from app.core.security import get_password_hash

# IMPORTANT: Import all models here so SQLAlchemy knows about them
from app.models.library import Library
from app.models.series import Series
from app.models.comic import Volume, Comic
from app.models.tags import Character, Team, Location
from app.models.credits import Person, ComicCredit
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.collection import Collection, CollectionItem
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.job import ScanJob
from app.models.user import User

from app.services.watcher import library_watcher

# API Routes
from app.api import libraries, comics, reader, progress, series, volumes, search
from app.api import reading_lists, collections
from app.api import jobs
from app.api import auth
from app.api import users
from app.api import tasks
from app.api import stats

# Frontend Routes (HTML)
from app.routers import pages, admin

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

    # --- NEW: Auto-Create Default Admin ---
    db = SessionLocal()
    try:
        user_count = db.query(User).count()
        if user_count == 0:
            print("No users found. Creating default admin...")
            default_admin = User(
                username="admin",
                email="admin@example.com",
                hashed_password=get_password_hash("admin"),  # Default password
                is_superuser=True,
                is_active=True
            )
            db.add(default_admin)
            db.commit()
            print("Created user: admin / admin")
    finally:
        db.close()
    # --------------------------------------

    # START WATCHER
    library_watcher.start()

    logger.info("Comic Server starting up...")
    logger.info("Frontend available at http://localhost:8000")
    logger.info("API docs available at http://localhost:8000/docs")

    yield
    # Shutdown
    logger.info("Comic Server shutting down...")

    # STOP WATCHER
    library_watcher.stop()
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


# --- ROUTER REGISTRATION ---

# 1. API Routers (JSON)
app.include_router(libraries.router, prefix="/api/libraries", tags=["libraries"])
app.include_router(series.router, prefix="/api/series", tags=["series"])
app.include_router(volumes.router, prefix="/api/volumes", tags=["volumes"])
app.include_router(comics.router, prefix="/api/comics", tags=["comics"])
app.include_router(reader.router, prefix="/api/reader", tags=["reader"])
app.include_router(reading_lists.router, prefix="/api/reading-lists", tags=["reading-lists"])
app.include_router(collections.router, prefix="/api/collections", tags=["collections"])
app.include_router(progress.router, prefix="/api/progress", tags=["progress"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(stats.router, prefix="/api/stats", tags=["stats"])

# 2. Frontend Routers (HTML)
# We don't use a prefix for 'pages' because they live at the root (/)
app.include_router(pages.router)

# 3. Admin Routers (HTML)
# We add the /admin prefix here so we don't have to type it in every route in admin.py
app.include_router(admin.router, prefix="/admin")

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "comic-server"}