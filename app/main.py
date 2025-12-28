import os
import multiprocessing
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import HTTPException
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import portalocker

from app.core.settings_loader import get_system_setting
from app.core.security import get_redirect_url
from app.core.templates import templates
from app.database import engine, Base
from app.config import settings
from app.database import SessionLocal
from app.logging import log_config
from app.core.security import get_password_hash
from app.services.settings_service import SettingsService
from app.services.scheduler import scheduler_service


from app.models.user import User

from app.services.watcher import library_watcher

# API Routes
from app.api import libraries, comics, reader, progress, series, volumes, search
from app.api import reading_lists, collections
from app.api import auth, users, saved_searches, smart_lists
from app.api import tasks, jobs, stats, settings as settings_api
from app.api import pull_lists
from app.api import reports
from app.api import migration
from app.api import batch
from app.api import home

# Frontend Routes (HTML)
from app.routers import pages, admin

# OPDS Routes
from app.routers import opds

# Setup logging
#logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger = log_config.setup_logging("INFO")

@asynccontextmanager
async def lifespan(app: FastAPI):

    # --- 1. GLOBAL SETUP (Run on ALL Uvicorn Workers) ---
    # Keep this OUTSIDE the guard so every worker process gets configured logging.
    # If we don't do this, workers will fall back to default console logging.

    # Silence Uvicorn's default access logger to reduce noise
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    # Ensure directories exist (Safe to run multiple times)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    Path("storage/database").mkdir(parents=True, exist_ok=True)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    settings.cover_dir.mkdir(parents=True, exist_ok=True)
    settings.avatar_dir.mkdir(parents=True, exist_ok=True)

    # -- Init DB defaults when necessary (Safe, idempotent)
    db = SessionLocal()
    try:
        SettingsService(db).initialize_defaults()
    finally:
        db.close()
    # --------------------------------------

    # SETUP LOGGING
    # Each worker configures its own logger instance pointing to the same file.
    log_level = get_system_setting("general.log_level", "INFO")
    logger = log_config.setup_logging(log_level)

    worker_pid = os.getpid()
    logger.info(f"Worker process PID:{worker_pid} startup (Log Level: {log_level})")

    # --- 2. SINGLETON SETUP (Run ONLY on Main Process, cross plat.) ---
    # This protection is strictly for the Watcher and Scheduler.
    # We don't want 4 workers all trying to scan the library at once.

    lock_file_path = settings.cache_dir / "scheduler.lock"
    lock_file_path.parent.mkdir(parents=True, exist_ok=True)

    # We open the file, but we don't close it until shutdown
    lock_file = open(lock_file_path, "w")
    is_manager = False

    try:
        # Portalocker handles the OS differences automatically.
        # LOCK_EX = Exclusive, LOCK_NB = Non-Blocking
        portalocker.lock(lock_file, portalocker.LOCK_EX | portalocker.LOCK_NB)
        is_manager = True

        logger.info(f"Worker {worker_pid} acquired Manager Lock. Starting Watcher & Scheduler...")

        # START WATCHER
        library_watcher.start()

        # START SCHEDULER
        scheduler_service.start()
    except:
        # Lock is held by another worker.
        logger.info(f"Worker {worker_pid} could not acquire lock. Skipping singletons.")

    yield

    # --- SHUTDOWN ---
    logger.info(f"Worker {worker_pid} shutting down...")

    if is_manager:
        logger.info(f"Worker {worker_pid} is Manager, also stopping services...")
        library_watcher.stop()
        scheduler_service.stop()

        # Release lock
        try:
            portalocker.unlock(lock_file)
            lock_file.close()
            # Optional: Remove file (safeish, but not strictly required)
            if lock_file_path.exists():
                os.remove(lock_file_path)
                logger.debug("Manager lock file deleted")
        except Exception as e:
            logger.error(f"Error releasing lock: {e}")


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
    root_path=settings.clean_base_url,
)

# CORS middleware (adjust origins as needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.trusted_proxies)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")



# Global exception handler
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):


    # --- Allow OPDS/API to handle their own Auth errors ---
    # If we are in the API or OPDS, do NOT redirect to the HTML login page.
    # We must return the raw JSON/Error so the Basic Auth header is sent to the client.
    if request.url.path.startswith(("/opds", "/api")):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=exc.headers  # CRITICAL: This passes the 'WWW-Authenticate' header
        )


    # Standard Web UI Logic
    # If HTML request and 401, redirect to Login
    if exc.status_code == 401 and "text/html" in request.headers.get("accept", ""):

        return_url = get_redirect_url(request.url.path, request.url.query)

        # Use settings.clean_base_url to ensure the subpath is included
        login_path = f"{settings.clean_base_url}/login?next={return_url}"

        return RedirectResponse(url=login_path)

    if "text/html" in request.headers.get("accept", ""):
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={"status_code": exc.status_code, "detail": exc.detail},
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
            request=request,
            name="error.html",
            context={"status_code": 500, "detail": "Internal server error"},
            status_code=500
        )
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"}
    )


# --- ROUTER REGISTRATION ---

# 1. API Routers (JSON)
app.include_router(home.router, prefix="/api/home", tags=["home"])
app.include_router(libraries.router, prefix="/api/libraries", tags=["libraries"])
app.include_router(series.router, prefix="/api/series", tags=["series"])
app.include_router(volumes.router, prefix="/api/volumes", tags=["volumes"])
app.include_router(comics.router, prefix="/api/comics", tags=["comics"])
app.include_router(reader.router, prefix="/api/reader", tags=["reader"])
app.include_router(reading_lists.router, prefix="/api/reading-lists", tags=["reading-lists"])
app.include_router(collections.router, prefix="/api/collections", tags=["collections"])
app.include_router(progress.router, prefix="/api/progress", tags=["progress"])
app.include_router(batch.router, prefix="/api/batch", tags=["batch"])
app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(settings_api.router, prefix="/api/settings", tags=["settings"])
app.include_router(saved_searches.router, prefix="/api/saved-searches", tags=["saved-searches"])
app.include_router(smart_lists.router, prefix="/api/smart-lists", tags=["smart-lists"])
app.include_router(pull_lists.router, prefix="/api/pull-lists", tags=["pull-lists"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])

# Pure admin routers
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks", "admin"])
app.include_router(stats.router, prefix="/api/stats", tags=["stats", "admin"])
app.include_router(reports.router, prefix="/api/reports", tags=["reports", "admin"])
app.include_router(migration.router, prefix="/api/migration", tags=["migration", "admin"])




# 2. Frontend Routers (HTML)
# We don't use a prefix for 'pages' because they live at the root (/)
app.include_router(pages.router, tags=["pages"])

# 3. Admin Routers (HTML)
# We add the /admin prefix here so we don't have to type it in every route in admin.py
app.include_router(admin.router, prefix="/admin", tags=["admin"])

# OPDS Routers
app.include_router(opds.router)

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "parker"}



