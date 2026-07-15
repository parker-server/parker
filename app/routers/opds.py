from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import Response, FileResponse
from sqlalchemy.orm import joinedload
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

from app.api.opds_deps import OPDSUser, SessionDep
from app.models import ComicCredit
from app.models.library import Library
from app.models.series import Series
from app.models.comic import Comic, Volume
from app.core.templates import templates
from app.core.comic_helpers import (
    get_series_age_restriction,
    get_comic_age_restriction,
    get_age_rating_config, NON_PLAIN_FORMATS, get_thumbnail_url
)

router = APIRouter(prefix="/opds", tags=["opds"])


OPDS_ACQUISITION_TYPES = {
    ".cbz": "application/vnd.comicbook+zip",
    ".zip": "application/vnd.comicbook+zip",
    ".cbr": "application/vnd.comicbook-rar",
    ".rar": "application/vnd.comicbook-rar",
    ".cb7": "application/x-7z-compressed",
    ".7z": "application/x-7z-compressed",
    ".pdf": "application/pdf",
}


def format_opds_datetime(value: datetime | None) -> str:
    """Return an RFC 3339 UTC timestamp for OPDS feeds."""
    if value is None:
        value = datetime.now(timezone.utc)
    elif value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def format_opds_issued(year: int | None, month: int | None, day: int | None) -> str | None:
    """Return a safe ISO date string for partial comic dates."""
    if not year:
        return None
    safe_month = month or 1
    safe_day = day or 1
    return f"{year:04d}-{safe_month:02d}-{safe_day:02d}"


def get_comic_archive_suffix(comic: Comic) -> str:
    """Return the normalized archive suffix for OPDS/download metadata."""
    filename = comic.filename or str(comic.file_path or "")
    return Path(filename).suffix.lower()


def get_opds_acquisition_type(comic: Comic) -> str:
    """Return the best OPDS acquisition MIME type for a comic file."""
    return OPDS_ACQUISITION_TYPES.get(get_comic_archive_suffix(comic), "application/octet-stream")


def get_opds_download_filename(comic: Comic) -> str:
    """Return a stable export filename that keeps the comic's real extension."""
    suffix = get_comic_archive_suffix(comic) or ".cbz"
    safe_title = comic.title or comic.filename or f"comic-{comic.id}"
    return f"{comic.series_group or 'Comic'} - {safe_title}{suffix}"


# Helper to render XML
def render_xml(request: Request, context: dict):
    return templates.TemplateResponse(
        request=request,
        name="opds/feed.xml",
        context=context,
        media_type="application/atom+xml;charset=utf-8"
    )

# Helper: Check if format is "Standard" (Not Annual/Special)
def is_standard_format(fmt: str) -> bool:
    if not fmt: return True
    f = fmt.lower()
    return f not in NON_PLAIN_FORMATS

# Helper: Safe Sort Key for issues
def issue_sort_key(c):
    try:
        return float(c.number)
    except:
        return 999999


# 1. ROOT: List Libraries
@router.get("/", name="root")
async def opds_root(request: Request, user: OPDSUser, db: SessionDep):

    # If Superuser, fetch ALL libraries. If regular user, use assigned.
    if user.is_superuser:
        libs = db.query(Library).all()
    else:
        # RLS: Only show accessible libraries
        libs = user.accessible_libraries

    entries = []
    for lib in libs:
        entries.append({
            "id": f"urn:parker:lib:{lib.id}",
            "title": lib.name,
            "updated": format_opds_datetime(datetime.now(timezone.utc)),  # Libraries rarely change, using now() is acceptable for root
            "link": f"/opds/libraries/{lib.id}",
            "summary": f"Library containing {len(lib.series)} series."
        })

    return render_xml(request, {
        "feed_id": "urn:parker:root",
        "feed_title": "Parker Library",
        "updated_at": format_opds_datetime(datetime.now(timezone.utc)),
        "entries": entries,
        "books": []
    })


# 2. LIBRARY: List Series
@router.get("/libraries/{library_id}", name="library")
async def opds_library(library_id: int, request: Request, user: OPDSUser, db: SessionDep):
    # Security check using your existing accessible_libraries logic

    if not user.is_superuser:
        allowed_ids = [l.id for l in user.accessible_libraries]
        if library_id not in allowed_ids:
            raise HTTPException(status_code=404, detail="Library not found")

    library = db.query(Library).filter(Library.id == library_id).first()

    # Fetch series
    query = db.query(Series).filter(Series.library_id == library_id)

    # --- AGE RESTRICTION (Poison Pill) ---
    age_filter = get_series_age_restriction(user)
    if age_filter is not None:
        query = query.filter(age_filter)
    # -------------------------------------

    series_list = query.order_by(Series.name).all()

    # Collect Series IDs
    series_ids = [s.id for s in series_list]
    # Fetch ALL Comics for these series in one go (Lightweight columns only)
    raw_comics = (
        db.query(Comic.id, Comic.number, Comic.year,
                 Comic.format, Comic.updated_at, Comic.thumbnail_path,
            Volume.series_id, Volume.volume_number
        ).join(Volume).filter(Volume.series_id.in_(series_ids)).all()
    )

    # Group Comics by Series in Python
    series_map = defaultdict(list)
    for row in raw_comics:
        series_map[row.series_id].append(row)

    entries = []
    for s in series_list:

        s_comics = series_map.get(s.id, [])
        series_year = min((c.year for c in s_comics if c.year), default=None)
        cover_comic = None
        if s_comics:
            # Filter for standards
            standards = [c for c in s_comics if is_standard_format(c.format)]
            pool = standards if standards else s_comics
            issue_ones = [c for c in pool if c.number == '1']
            if issue_ones:
                issue_ones.sort(key=lambda c: c.volume_number)
                cover_comic = issue_ones[0]
            else:
                # Fallback: Sort by number
                pool.sort(key=issue_sort_key)
                cover_comic = pool[0]


        entries.append({
            "id": f"urn:parker:series:{s.id}",
            "title": f"{s.name} ({series_year})" if series_year else s.name,
            "updated": format_opds_datetime(s.updated_at),
            "link": f"/opds/series/{s.id}",
            "summary": s.summary_override,
            "thumbnail": get_thumbnail_url(cover_comic.id, cover_comic.updated_at) if cover_comic else None,
        })

    return render_xml(request, {
        "feed_id": f"urn:parker:lib:{library_id}",
        "feed_title": library.name,
        "updated_at": format_opds_datetime(datetime.now(timezone.utc)),
        "entries": entries,
        "books": []
    })


# 3. SERIES: List Comics (Flattening Volumes)

@router.get("/series/{series_id}", name="series")
async def opds_series(series_id: int, request: Request, user: OPDSUser, db: SessionDep):

    # Security check for Series existence and Library Access would ideally happen here too
    # Assuming 'get_series_age_restriction' at library level helps, but let's be strict.

    # Fetch comics with RICH metadata
    query = (
        db.query(Comic)
        .join(Volume)
        .join(Series) # Explicit join for filtering
        .filter(Volume.series_id == series_id)
    )

    # --- AGE RESTRICTION (Filter Comics) ---
    age_filter = get_comic_age_restriction(user)
    if age_filter is not None:
        query = query.filter(age_filter)
    # ---------------------------------------

    comics = query.options(
            joinedload(Comic.credits).joinedload(ComicCredit.person), # Load credits + person names
            joinedload(Comic.genres),    # Load Genres
            joinedload(Comic.volume).joinedload(Volume.series) # Load Series Name
        ).order_by(Volume.volume_number, Comic.number).all()

    # If all comics are restricted, handle empty list gracefully
    feed_title = "Series"
    if comics:
        feed_title = comics[0].volume.series.name
    else:
        # Fallback fetch name if empty (optional)
        s = db.query(Series.name).filter(Series.id == series_id).scalar()
        if s: feed_title = s

    return render_xml(request, {
        "feed_id": f"urn:parker:series:{series_id}",
        "feed_title": feed_title,
        "updated_at": format_opds_datetime(datetime.now(timezone.utc)),
        "entries": [],
        "books": comics
    })


templates.env.globals["format_opds_datetime"] = format_opds_datetime
templates.env.globals["format_opds_issued"] = format_opds_issued
templates.env.globals["get_opds_acquisition_type"] = get_opds_acquisition_type


# 4. DOWNLOAD: Serve the file
@router.get("/download/{comic_id}", name="download")
async def opds_download(comic_id: int, user: OPDSUser, db: SessionDep):
    # We duplicate the logic from get_secure_comic here because we need
    # to authenticate via Basic Auth (user argument), not JWT.

    comic = db.query(Comic).join(Volume).join(Series).filter(Comic.id == comic_id).first()

    if not comic:
        raise HTTPException(status_code=404)

    if not user.is_superuser:
        if comic.volume.series.library_id not in [l.id for l in user.accessible_libraries]:
            raise HTTPException(status_code=404)

    # 2. Age Rating Check
    if not user.is_superuser and user.max_age_rating:

        allowed, banned = get_age_rating_config(user)

        is_restricted = False

        if comic.age_rating in banned: is_restricted = True

        if not user.allow_unknown_age_ratings:
            if not comic.age_rating or comic.age_rating == "" or comic.age_rating.lower() == "unknown":
                is_restricted = True

        if is_restricted:
            raise HTTPException(status_code=403, detail="Age Restricted")


    export_name = get_opds_download_filename(comic)

    return FileResponse(
        path=str(comic.file_path),
        filename=export_name,
        media_type=get_opds_acquisition_type(comic),
        headers={"Content-Disposition": f'attachment; filename="{export_name}"'}
    )
