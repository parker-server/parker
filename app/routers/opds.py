from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import Response, FileResponse
from sqlalchemy import func
from sqlalchemy.orm import joinedload
from datetime import datetime, timezone
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
from PIL import Image

from app.api.deps import PaginationParams
from app.api.opds_deps import OPDSUser, SessionDep
from app.models import ComicCredit
from app.models.library import Library
from app.models.series import Series
from app.models.comic import Comic, Volume
from app.core.templates import templates
from app.core.comic_helpers import (
    get_series_age_restriction,
    get_comic_age_restriction,
    get_age_rating_config, NON_PLAIN_FORMATS, get_thumbnail_hash
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

INVALID_FILENAME_CHARS = set('<>:"/\\|?*')
OPDS_CATALOG_FEED_TYPE = "application/atom+xml;profile=opds-catalog"


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
    return Path(comic.filename).suffix.lower()


def get_opds_acquisition_type(comic: Comic) -> str:
    """Return the best OPDS acquisition MIME type for a comic file."""
    return OPDS_ACQUISITION_TYPES.get(get_comic_archive_suffix(comic), "application/octet-stream")


def sanitize_opds_filename(value: str) -> str:
    """Return a header/path-safe filename while preserving readable titles."""
    cleaned = "".join("_" if char in INVALID_FILENAME_CHARS or ord(char) < 32 else char for char in value)
    return " ".join(cleaned.split()).strip(" .") or "comic"


def get_opds_download_filename(comic: Comic) -> str:
    """Return a stable export filename that keeps the comic's real extension."""
    suffix = get_comic_archive_suffix(comic) or ".cbz"
    if comic.title:
        safe_title = comic.title
    elif comic.filename:
        safe_title = Path(comic.filename).stem or comic.filename
    else:
        safe_title = f"comic-{comic.id}"
    return sanitize_opds_filename(f"{comic.series_group or 'Comic'} - {safe_title}{suffix}")


def get_opds_download_href(request: Request, comic: Comic) -> str:
    """Return an absolute, filename-bearing download URL for OPDS acquisition links."""
    return str(
        request.url_for(
            "opds_download_named",
            comic_id=comic.id,
            filename=quote(get_opds_download_filename(comic), safe=""),
        )
    )


def get_opds_thumbnail_href(request: Request, comic: Comic) -> str:
    """Return an absolute, JPEG thumbnail URL for OPDS clients."""
    url = str(request.url_for("opds_thumbnail", comic_id=comic.id))
    return f"{url}?v={get_thumbnail_hash(comic.updated_at)}"


def get_authorized_opds_comic(comic_id: int, user: OPDSUser, db: SessionDep) -> Comic:
    """Return a comic if the OPDS user can access it, otherwise raise."""
    comic = (
        db.query(Comic)
        .join(Volume)
        .join(Series)
        .options(joinedload(Comic.library_root))
        .filter(Comic.id == comic_id)
        .first()
    )

    if not comic:
        raise HTTPException(status_code=404)

    if not user.is_superuser:
        if comic.volume.series.library_id not in [l.id for l in user.accessible_libraries]:
            raise HTTPException(status_code=404)

    if not user.is_superuser and user.max_age_rating:
        _, banned = get_age_rating_config(user)
        is_restricted = False

        if comic.age_rating in banned:
            is_restricted = True

        if not user.allow_unknown_age_ratings:
            if not comic.age_rating or comic.age_rating == "" or comic.age_rating.lower() == "unknown":
                is_restricted = True

        if is_restricted:
            raise HTTPException(status_code=403, detail="Age Restricted")

    return comic


def get_opds_thumbnail_path(comic: Comic) -> Path | None:
    """Return the cached thumbnail path, matching the public thumbnail fallback."""
    if comic.thumbnail_path:
        db_path = Path(comic.thumbnail_path)
        if db_path.exists():
            return db_path

    standard_path = Path(f"./storage/cover/comic_{comic.id}.webp")
    if standard_path.exists():
        return standard_path

    return None


def get_opds_pagination_links(request: Request, total: int, params: PaginationParams) -> list[dict[str, str]]:
    """Return Atom pagination links for OPDS feeds."""
    total_pages = max(1, (total + params.size - 1) // params.size)

    def page_url(page: int) -> str:
        return str(request.url.include_query_params(page=page, size=params.size))

    links = [
        {"rel": "self", "href": page_url(params.page), "type": OPDS_CATALOG_FEED_TYPE},
        {"rel": "first", "href": page_url(1), "type": OPDS_CATALOG_FEED_TYPE},
        {"rel": "last", "href": page_url(total_pages), "type": OPDS_CATALOG_FEED_TYPE},
    ]

    if params.page > 1:
        links.append({"rel": "previous", "href": page_url(params.page - 1), "type": OPDS_CATALOG_FEED_TYPE})

    if params.page < total_pages:
        links.append({"rel": "next", "href": page_url(params.page + 1), "type": OPDS_CATALOG_FEED_TYPE})

    return links


# Helper to render XML
def render_xml(request: Request, context: dict):
    context.setdefault("feed_links", [])
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

    # Batch-count series per library instead of lazy-loading lib.series per iteration (avoids N+1)
    series_counts = dict(
        db.query(Series.library_id, func.count(Series.id))
        .filter(Series.library_id.in_([lib.id for lib in libs]))
        .group_by(Series.library_id)
        .all()
    ) if libs else {}

    entries = []
    for lib in libs:
        entries.append({
            "id": f"urn:parker:lib:{lib.id}",
            "title": lib.name,
            "updated": format_opds_datetime(datetime.now(timezone.utc)),  # Libraries rarely change, using now() is acceptable for root
            "link": str(request.url_for("library", library_id=lib.id)),
            "summary": f"Library containing {series_counts.get(lib.id, 0)} series."
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
async def opds_library(
        library_id: int,
        request: Request,
        user: OPDSUser,
        db: SessionDep,
        params: Annotated[PaginationParams, Depends()]
):
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

    total = query.count()
    series_list = query.order_by(Series.name).offset(params.skip).limit(params.size).all()

    # Collect Series IDs
    series_ids = [s.id for s in series_list]
    # Fetch ALL Comics for these series in one go (Lightweight columns only)
    raw_comics = []
    if series_ids:
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
            "link": str(request.url_for("series", series_id=s.id)),
            "summary": s.summary_override,
            "thumbnail": get_opds_thumbnail_href(request, cover_comic) if cover_comic else None,
        })

    return render_xml(request, {
        "feed_id": f"urn:parker:lib:{library_id}",
        "feed_title": library.name,
        "updated_at": format_opds_datetime(datetime.now(timezone.utc)),
        "feed_links": get_opds_pagination_links(request, total, params),
        "entries": entries,
        "books": []
    })


# 3. SERIES: List Comics (Flattening Volumes)

@router.get("/series/{series_id}", name="series")
async def opds_series(
        series_id: int,
        request: Request,
        user: OPDSUser,
        db: SessionDep,
        params: Annotated[PaginationParams, Depends()]
):

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

    total = query.count()
    comics = query.options(
            joinedload(Comic.credits).joinedload(ComicCredit.person), # Load credits + person names
            joinedload(Comic.genres),    # Load Genres
            joinedload(Comic.volume).joinedload(Volume.series) # Load Series Name
        ).order_by(Volume.volume_number, Comic.number).offset(params.skip).limit(params.size).all()

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
        "feed_links": get_opds_pagination_links(request, total, params),
        "entries": [],
        "books": comics
    })


templates.env.globals["format_opds_datetime"] = format_opds_datetime
templates.env.globals["format_opds_issued"] = format_opds_issued
templates.env.globals["get_opds_acquisition_type"] = get_opds_acquisition_type
templates.env.globals["get_opds_download_href"] = get_opds_download_href
templates.env.globals["get_opds_thumbnail_href"] = get_opds_thumbnail_href


# 4. DOWNLOAD: Serve the file
@router.get("/images/{comic_id}/thumbnail.jpg", name="opds_thumbnail")
async def opds_thumbnail(comic_id: int, db: SessionDep):
    comic = db.query(Comic).filter(Comic.id == comic_id).first()
    if not comic:
        raise HTTPException(status_code=404, detail="Comic not found")

    thumbnail_path = get_opds_thumbnail_path(comic)

    if not thumbnail_path:
        raise HTTPException(status_code=404, detail="Could not find thumbnail")

    try:
        with Image.open(thumbnail_path) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")

            output = BytesIO()
            img.save(output, format="JPEG", quality=88)
    except OSError:
        raise HTTPException(status_code=404, detail="Could not read thumbnail")

    last_mod = int(comic.updated_at.timestamp()) if comic.updated_at else 0
    return Response(
        content=output.getvalue(),
        media_type="image/jpeg",
        headers={
            "ETag": f'"opds-thumb-{comic_id}-{last_mod}"',
            "Cache-Control": "public, max-age=31536000",
            "Content-Disposition": f'inline; filename="comic_{comic_id}_thumbnail.jpg"',
        },
    )


@router.get("/download/{comic_id}/{filename}", name="opds_download_named")
@router.get("/download/{comic_id}", name="download")
async def opds_download(comic_id: int, user: OPDSUser, db: SessionDep, filename: str | None = None):
    comic = get_authorized_opds_comic(comic_id, user, db)

    export_name = get_opds_download_filename(comic)

    return FileResponse(
        path=comic.absolute_path,
        filename=export_name,
        media_type=get_opds_acquisition_type(comic),
        headers={"Content-Disposition": f'attachment; filename="{export_name}"'}
    )
