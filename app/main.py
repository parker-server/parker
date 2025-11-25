from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
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

from app.api import libraries, comics, reader, reading_lists


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print(f"Starting {settings.app_name}")

    # Ensure directories exist
    Path("storage/database").mkdir(parents=True, exist_ok=True)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)

    # Create database tables (models are now imported)
    Base.metadata.create_all(bind=engine)

    yield
    # Shutdown
    print("Shutting down")


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")

# Include routers
app.include_router(libraries.router, prefix="/libraries", tags=["libraries"])
app.include_router(comics.router, prefix="/comics", tags=["comics"])
app.include_router(reader.router, prefix="/reader", tags=["reader"])

app.include_router(reading_lists.router, prefix="/reading-lists", tags=["reading-lists"])  # Add this


@app.get("/")
async def root():
    return {"message": "Comic Server API"}