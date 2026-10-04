from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import Response, FileResponse
from sqlalchemy import Float, func, desc, not_, select
from sqlalchemy.orm import aliased, joinedload
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
from PIL import Image

from app.api.deps import PaginationParams
from app.api.opds_deps import OPDSUser, SessionDep
from app.core.settings_loader import get_cached_setting
from app.models import ComicCredit
from app.models.collection import Collection, CollectionItem
from app.models.library import Library
from app.models.series import Series
from app.models.comic import Comic, Volume
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.reading_progress import ReadingProgress
from app.core.templates import templates
from app.core.comic_helpers import (
    get_series_age_restriction,
    get_comic_age_restriction,
    assert_user_can_view_comic,
    check_container_restriction,
    get_banned_comic_condition,
    NON_PLAIN_FORMATS,
    get_thumbnail_hash,
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


def get_authorized_opds_comic(
        comic_id: int,
        user: OPDSUser,
        db: SessionDep,
        *,
        hide_denied: bool = False,
) -> Comic:
    """Return a comic if the OPDS user can access it, otherwise raise."""
    comic = (
        db.query(Comic)
        .join(Volume)
        .join(Series)
        .options(
            joinedload(Comic.library_root),
            joinedload(Comic.volume).joinedload(Volume.series),
        )
        .filter(Comic.id == comic_id)
        .first()
    )

    if not comic:
        raise HTTPException(status_code=404)

    assert_user_can_view_comic(comic, user, hide_denied=hide_denied)

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


def get_opds_user_library_ids(user: OPDSUser) -> list[int]:
    """Return library IDs visible to this OPDS user."""
    return [library.id for library in user.accessible_libraries]


def filter_opds_query_to_user_libraries(query, user: OPDSUser):
    """Apply OPDS library access filtering to a query already joined to Series."""
    if user.is_superuser:
        return query
    return query.filter(Series.library_id.in_(get_opds_user_library_ids(user)))


def get_authorized_opds_series(series_id: int, user: OPDSUser, db: SessionDep) -> Series:
    """Return a series if the OPDS user can access its library."""
    query = db.query(Series).filter(Series.id == series_id)
    if not user.is_superuser:
        query = query.filter(Series.library_id.in_(get_opds_user_library_ids(user)))

    series = query.first()
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")

    return series


def get_authorized_opds_volume(volume_id: int, user: OPDSUser, db: SessionDep) -> Volume:
    """Return a volume if the OPDS user can access its parent library."""
    query = (
        db.query(Volume)
        .join(Series)
        .options(joinedload(Volume.series))
        .filter(Volume.id == volume_id)
    )
    if not user.is_superuser:
        query = query.filter(Series.library_id.in_(get_opds_user_library_ids(user)))

    volume = query.first()
    if not volume:
        raise HTTPException(status_code=404, detail="Volume not found")

    return volume


def select_opds_cover_comic(comics_list):
    """Select a stable representative cover comic from lightweight comic rows."""
    if not comics_list:
        return None

    standards = [comic for comic in comics_list if is_standard_format(comic.format)]
    pool = standards if standards else list(comics_list)
    issue_ones = [comic for comic in pool if comic.number == '1']
    if issue_ones:
        issue_ones.sort(key=lambda comic: comic.volume_number or 0)
        return issue_ones[0]

    pool.sort(key=issue_sort_key)
    return pool[0]


def get_series_comics_map(db: SessionDep, series_ids: list[int]) -> dict[int, list]:
    """Return lightweight comics grouped by series for OPDS navigation entries."""
    if not series_ids:
        return {}

    raw_comics = (
        db.query(
            Comic.id,
            Comic.number,
            Comic.year,
            Comic.format,
            Comic.updated_at,
            Comic.thumbnail_path,
            Comic.volume_id,
            Volume.series_id,
            Volume.volume_number,
        )
        .join(Volume)
        .filter(Volume.series_id.in_(series_ids))
        .all()
    )

    series_map = defaultdict(list)
    for row in raw_comics:
        series_map[row.series_id].append(row)

    return series_map


def get_opds_series_entries(
        request: Request,
        db: SessionDep,
        series_list: list[Series],
        *,
        cover_volume_by_series_id: dict[int, int] | None = None,
) -> list[dict]:
    """Return OPDS navigation entries for series with representative thumbnails."""
    series_map = get_series_comics_map(db, [series.id for series in series_list])

    entries = []
    for series in series_list:
        series_comics = series_map.get(series.id, [])
        cover_volume_id = cover_volume_by_series_id.get(series.id) if cover_volume_by_series_id else None
        cover_candidates = (
            [comic for comic in series_comics if comic.volume_id == cover_volume_id]
            if cover_volume_id else series_comics
        )
        cover_comic = select_opds_cover_comic(cover_candidates) or select_opds_cover_comic(series_comics)
        series_year = min((comic.year for comic in series_comics if comic.year), default=None)

        entries.append({
            "id": f"urn:parker:series:{series.id}",
            "title": f"{series.name} ({series_year})" if series_year else series.name,
            "updated": format_opds_datetime(series.updated_at),
            "link": str(request.url_for("series", series_id=series.id)),
            "summary": series.summary_override,
            "thumbnail": get_opds_thumbnail_href(request, cover_comic) if cover_comic else None,
        })

    return entries


def get_volume_comics_map(db: SessionDep, volume_ids: list[int], user: OPDSUser) -> dict[int, list]:
    """Return lightweight, visible comics grouped by volume for OPDS navigation entries."""
    if not volume_ids:
        return {}

    query = (
        db.query(
            Comic.id,
            Comic.number,
            Comic.year,
            Comic.format,
            Comic.updated_at,
            Comic.thumbnail_path,
            Comic.volume_id,
            Volume.series_id,
            Volume.volume_number,
        )
        .join(Volume)
        .filter(Comic.volume_id.in_(volume_ids))
    )

    age_filter = get_comic_age_restriction(user)
    if age_filter is not None:
        query = query.filter(age_filter)

    volume_map = defaultdict(list)
    for row in query.all():
        volume_map[row.volume_id].append(row)

    return volume_map


def get_opds_volume_entries(request: Request, db: SessionDep, volumes: list[Volume], user: OPDSUser) -> list[dict]:
    """Return OPDS navigation entries for volumes."""
    volume_map = get_volume_comics_map(db, [volume.id for volume in volumes], user)

    entries = []
    for volume in volumes:
        volume_comics = volume_map.get(volume.id, [])
        cover_comic = select_opds_cover_comic(volume_comics)
        updated_at = max((comic.updated_at for comic in volume_comics if comic.updated_at), default=None)
        volume_number = volume.volume_number if volume.volume_number is not None else "Unknown"

        entries.append({
            "id": f"urn:parker:volume:{volume.id}",
            "title": f"Volume {volume_number}",
            "updated": format_opds_datetime(updated_at),
            "link": str(request.url_for("opds_volume", volume_id=volume.id)),
            "summary": volume.summary_override,
            "thumbnail": get_opds_thumbnail_href(request, cover_comic) if cover_comic else None,
        })

    return entries


def get_latest_event_volume_by_series_id(db: SessionDep, series_ids: list[int], timestamp_column) -> dict[int, int]:
    """Return the volume containing the latest timestamped comic per series."""
    if not series_ids:
        return {}

    rows = (
        db.query(
            Volume.series_id,
            Comic.volume_id,
            timestamp_column.label("event_at"),
        )
        .join(Volume)
        .filter(Volume.series_id.in_(series_ids))
        .order_by(
            desc(timestamp_column),
            desc(Volume.volume_number),
            desc(Comic.id),
        )
        .all()
    )

    latest_volume_by_series_id = {}
    for row in rows:
        latest_volume_by_series_id.setdefault(row.series_id, row.volume_id)

    return latest_volume_by_series_id


def get_visible_opds_comics_query(db: SessionDep, user: OPDSUser):
    """Return a Comic query joined to Volume and Series with OPDS visibility applied."""
    query = db.query(Comic).join(Volume).join(Series)
    query = filter_opds_query_to_user_libraries(query, user)

    age_filter = get_comic_age_restriction(user)
    if age_filter is not None:
        query = query.filter(age_filter)

    return query


def get_opds_books_query_options():
    """Return eager-load options required by the OPDS acquisition entry template."""
    return (
        joinedload(Comic.credits).joinedload(ComicCredit.person),
        joinedload(Comic.genres),
        joinedload(Comic.volume).joinedload(Volume.series),
    )


def render_opds_books_feed(
        request: Request,
        *,
        feed_id: str,
        feed_title: str,
        query,
        total: int,
        params: PaginationParams,
        entries: list[dict] | None = None,
        order_by: tuple = (),
):
    """Render a paginated OPDS acquisition feed from a Comic query."""
    if order_by:
        query = query.order_by(*order_by)

    comics = (
        query
        .options(*get_opds_books_query_options())
        .offset(params.skip)
        .limit(params.size)
        .all()
    )

    return render_xml(request, {
        "feed_id": feed_id,
        "feed_title": feed_title,
        "updated_at": format_opds_datetime(datetime.now(timezone.utc)),
        "feed_links": get_opds_pagination_links(request, total, params),
        "entries": entries or [],
        "books": comics,
    })


def get_opds_issue_count_summary(description: str | None, issue_count: int) -> str:
    """Return a concise OPDS navigation summary for a comic container."""
    issue_word = "issue" if issue_count == 1 else "issues"
    count_summary = f"{issue_count} {issue_word}."
    if description:
        return f"{description}\n\n{count_summary}"
    return count_summary


def get_collection_visible_count_col(user: OPDSUser):
    """Return a correlated visible-item count for OPDS collection list entries."""
    item_alias = aliased(CollectionItem)
    count_stmt = (
        select(func.count(item_alias.id))
        .join(Comic, item_alias.comic_id == Comic.id)
        .join(Volume, Comic.volume_id == Volume.id)
        .join(Series, Volume.series_id == Series.id)
        .join(Library, Series.library_id == Library.id)
        .where(Library.parse_collections == True)
        .where(item_alias.collection_id == Collection.id)
    )

    if not user.is_superuser:
        count_stmt = count_stmt.where(Series.library_id.in_(get_opds_user_library_ids(user)))

    series_age_filter = get_series_age_restriction(user)
    if series_age_filter is not None:
        count_stmt = count_stmt.where(series_age_filter)

    return count_stmt.scalar_subquery()


def get_reading_list_visible_count_col(user: OPDSUser):
    """Return a correlated visible-item count for OPDS reading-list entries."""
    item_alias = aliased(ReadingListItem)
    count_stmt = (
        select(func.count(item_alias.id))
        .join(Comic, item_alias.comic_id == Comic.id)
        .join(Volume, Comic.volume_id == Volume.id)
        .join(Series, Volume.series_id == Series.id)
        .join(Library, Series.library_id == Library.id)
        .where(Library.parse_reading_lists == True)
        .where(item_alias.reading_list_id == ReadingList.id)
    )

    if not user.is_superuser:
        count_stmt = count_stmt.where(Series.library_id.in_(get_opds_user_library_ids(user)))

    return count_stmt.scalar_subquery()


def get_opds_collection_entries(request: Request, rows: list[tuple[Collection, int]]) -> list[dict]:
    """Return OPDS navigation entries for visible collections."""
    entries = []
    for collection, visible_count in rows:
        entries.append({
            "id": f"urn:parker:collection:{collection.id}",
            "title": collection.name,
            "updated": format_opds_datetime(collection.updated_at),
            "link": str(request.url_for("opds_collection", collection_id=collection.id)),
            "summary": get_opds_issue_count_summary(collection.description, int(visible_count or 0)),
        })
    return entries


def get_opds_reading_list_entries(request: Request, rows: list[tuple[ReadingList, int]]) -> list[dict]:
    """Return OPDS navigation entries for visible reading lists."""
    entries = []
    for reading_list, visible_count in rows:
        entries.append({
            "id": f"urn:parker:reading-list:{reading_list.id}",
            "title": reading_list.name,
            "updated": format_opds_datetime(reading_list.updated_at),
            "link": str(request.url_for("opds_reading_list", list_id=reading_list.id)),
            "summary": get_opds_issue_count_summary(reading_list.description, int(visible_count or 0)),
        })
    return entries


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

    entries = [
        {
            "id": "urn:parker:continue-reading",
            "title": "Continue Reading",
            "updated": format_opds_datetime(datetime.now(timezone.utc)),
            "link": str(request.url_for("opds_continue_reading")),
            "summary": "Issues you have started but not finished.",
        },
        {
            "id": "urn:parker:recently-added",
            "title": "Recently Added",
            "updated": format_opds_datetime(datetime.now(timezone.utc)),
            "link": str(request.url_for("opds_recently_added")),
            "summary": "Series with newly imported comics.",
        },
        {
            "id": "urn:parker:recently-updated",
            "title": "Recently Updated",
            "updated": format_opds_datetime(datetime.now(timezone.utc)),
            "link": str(request.url_for("opds_recently_updated")),
            "summary": "Series with recently updated comics.",
        },
        {
            "id": "urn:parker:collections",
            "title": "Collections",
            "updated": format_opds_datetime(datetime.now(timezone.utc)),
            "link": str(request.url_for("opds_collections")),
            "summary": "Thematic comic collections.",
        },
        {
            "id": "urn:parker:reading-lists",
            "title": "Reading Lists",
            "updated": format_opds_datetime(datetime.now(timezone.utc)),
            "link": str(request.url_for("opds_reading_lists")),
            "summary": "Ordered reading lists and events.",
        },
    ]
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
    if not library:
        raise HTTPException(status_code=404, detail="Library not found")

    # Fetch series
    query = db.query(Series).filter(Series.library_id == library_id)

    # --- AGE RESTRICTION (Poison Pill) ---
    age_filter = get_series_age_restriction(user)
    if age_filter is not None:
        query = query.filter(age_filter)
    # -------------------------------------

    total = query.count()
    series_list = query.order_by(Series.name).offset(params.skip).limit(params.size).all()

    entries = get_opds_series_entries(request, db, series_list)

    return render_xml(request, {
        "feed_id": f"urn:parker:lib:{library_id}",
        "feed_title": library.name,
        "updated_at": format_opds_datetime(datetime.now(timezone.utc)),
        "feed_links": get_opds_pagination_links(request, total, params),
        "entries": entries,
        "books": []
    })


@router.get("/continue-reading", name="opds_continue_reading")
async def opds_continue_reading(
        request: Request,
        user: OPDSUser,
        db: SessionDep,
        params: Annotated[PaginationParams, Depends()]
):
    staleness_weeks = get_cached_setting("ui.on_deck.staleness_weeks", default=4)
    cutoff_date = None
    if staleness_weeks > 0:
        cutoff_date = datetime.now(timezone.utc) - timedelta(weeks=staleness_weeks)

    query = (
        db.query(Comic)
        .join(ReadingProgress, ReadingProgress.comic_id == Comic.id)
        .join(Volume)
        .join(Series)
        .filter(
            ReadingProgress.user_id == user.id,
            ReadingProgress.completed == False,
            ReadingProgress.current_page > 0,
        )
    )
    query = filter_opds_query_to_user_libraries(query, user)

    age_filter = get_series_age_restriction(user)
    if age_filter is not None:
        query = query.filter(age_filter)

    if cutoff_date:
        query = query.filter(ReadingProgress.last_read_at >= cutoff_date)

    total = query.count()
    return render_opds_books_feed(
        request,
        feed_id="urn:parker:continue-reading",
        feed_title="Continue Reading",
        query=query,
        total=total,
        params=params,
        order_by=(desc(ReadingProgress.last_read_at),),
    )


def render_recent_opds_series_feed(
        request: Request,
        user: OPDSUser,
        db: SessionDep,
        params: PaginationParams,
        *,
        timestamp_column,
        feed_id: str,
        feed_title: str,
):
    latest_event = (
        db.query(
            Volume.series_id.label("series_id"),
            func.max(timestamp_column).label("event_at"),
        )
        .select_from(Comic)
        .join(Volume)
        .group_by(Volume.series_id)
        .subquery()
    )

    query = db.query(Series).join(latest_event, latest_event.c.series_id == Series.id)
    query = filter_opds_query_to_user_libraries(query, user)

    age_filter = get_series_age_restriction(user)
    if age_filter is not None:
        query = query.filter(age_filter)

    total = query.count()
    series_list = (
        query
        .order_by(desc(latest_event.c.event_at), Series.name.asc())
        .offset(params.skip)
        .limit(params.size)
        .all()
    )
    cover_volumes = get_latest_event_volume_by_series_id(
        db,
        [series.id for series in series_list],
        timestamp_column,
    )

    return render_xml(request, {
        "feed_id": feed_id,
        "feed_title": feed_title,
        "updated_at": format_opds_datetime(datetime.now(timezone.utc)),
        "feed_links": get_opds_pagination_links(request, total, params),
        "entries": get_opds_series_entries(
            request,
            db,
            series_list,
            cover_volume_by_series_id=cover_volumes,
        ),
        "books": [],
    })


@router.get("/recently-added", name="opds_recently_added")
async def opds_recently_added(
        request: Request,
        user: OPDSUser,
        db: SessionDep,
        params: Annotated[PaginationParams, Depends()]
):
    return render_recent_opds_series_feed(
        request,
        user,
        db,
        params,
        timestamp_column=Comic.created_at,
        feed_id="urn:parker:recently-added",
        feed_title="Recently Added",
    )


@router.get("/recently-updated", name="opds_recently_updated")
async def opds_recently_updated(
        request: Request,
        user: OPDSUser,
        db: SessionDep,
        params: Annotated[PaginationParams, Depends()]
):
    return render_recent_opds_series_feed(
        request,
        user,
        db,
        params,
        timestamp_column=Comic.updated_at,
        feed_id="urn:parker:recently-updated",
        feed_title="Recently Updated",
    )


@router.get("/collections", name="opds_collections")
async def opds_collections(
        request: Request,
        user: OPDSUser,
        db: SessionDep,
        params: Annotated[PaginationParams, Depends()]
):
    visible_count_col = get_collection_visible_count_col(user)
    query = (
        db.query(Collection, visible_count_col.label("visible_count"))
        .filter(visible_count_col > 0)
    )

    banned_condition = get_banned_comic_condition(user)
    if banned_condition is not None:
        query = query.filter(not_(Collection.items.any(CollectionItem.comic.has(banned_condition))))

    total = query.count()
    rows = (
        query
        .order_by(Collection.name)
        .offset(params.skip)
        .limit(params.size)
        .all()
    )

    return render_xml(request, {
        "feed_id": "urn:parker:collections",
        "feed_title": "Collections",
        "updated_at": format_opds_datetime(datetime.now(timezone.utc)),
        "feed_links": get_opds_pagination_links(request, total, params),
        "entries": get_opds_collection_entries(request, rows),
        "books": [],
    })


@router.get("/collections/{collection_id}", name="opds_collection")
async def opds_collection(
        collection_id: int,
        request: Request,
        user: OPDSUser,
        db: SessionDep,
        params: Annotated[PaginationParams, Depends()]
):
    check_container_restriction(
        db,
        user,
        CollectionItem,
        CollectionItem.collection_id,
        collection_id,
        "Collection",
    )

    collection = db.query(Collection).filter(Collection.id == collection_id).first()
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    query = (
        db.query(Comic)
        .join(CollectionItem, CollectionItem.comic_id == Comic.id)
        .join(Volume)
        .join(Series)
        .join(Library)
        .filter(
            CollectionItem.collection_id == collection_id,
            Library.parse_collections == True,
        )
    )
    query = filter_opds_query_to_user_libraries(query, user)

    total = query.count()
    if total <= 0:
        raise HTTPException(status_code=404, detail="No comics found")

    return render_opds_books_feed(
        request,
        feed_id=f"urn:parker:collection:{collection.id}",
        feed_title=collection.name,
        query=query,
        total=total,
        params=params,
        order_by=(Comic.year.asc(), Series.name.asc(), func.cast(Comic.number, Float)),
    )


@router.get("/reading-lists", name="opds_reading_lists")
async def opds_reading_lists(
        request: Request,
        user: OPDSUser,
        db: SessionDep,
        params: Annotated[PaginationParams, Depends()]
):
    visible_count_col = get_reading_list_visible_count_col(user)
    query = (
        db.query(ReadingList, visible_count_col.label("visible_count"))
        .filter(visible_count_col > 0)
    )

    banned_condition = get_banned_comic_condition(user)
    if banned_condition is not None:
        query = query.filter(not_(ReadingList.items.any(ReadingListItem.comic.has(banned_condition))))

    total = query.count()
    rows = (
        query
        .order_by(ReadingList.name)
        .offset(params.skip)
        .limit(params.size)
        .all()
    )

    return render_xml(request, {
        "feed_id": "urn:parker:reading-lists",
        "feed_title": "Reading Lists",
        "updated_at": format_opds_datetime(datetime.now(timezone.utc)),
        "feed_links": get_opds_pagination_links(request, total, params),
        "entries": get_opds_reading_list_entries(request, rows),
        "books": [],
    })


@router.get("/reading-lists/{list_id}", name="opds_reading_list")
async def opds_reading_list(
        list_id: int,
        request: Request,
        user: OPDSUser,
        db: SessionDep,
        params: Annotated[PaginationParams, Depends()]
):
    check_container_restriction(
        db,
        user,
        ReadingListItem,
        ReadingListItem.reading_list_id,
        list_id,
        "Reading list",
    )

    reading_list = db.query(ReadingList).filter(ReadingList.id == list_id).first()
    if not reading_list:
        raise HTTPException(status_code=404, detail="Reading list not found")

    query = (
        db.query(Comic)
        .join(ReadingListItem, ReadingListItem.comic_id == Comic.id)
        .join(Volume)
        .join(Series)
        .join(Library)
        .filter(
            ReadingListItem.reading_list_id == list_id,
            Library.parse_reading_lists == True,
        )
    )
    query = filter_opds_query_to_user_libraries(query, user)

    total = query.count()
    if total <= 0:
        raise HTTPException(status_code=404, detail="No comics found (or access denied)")

    return render_opds_books_feed(
        request,
        feed_id=f"urn:parker:reading-list:{reading_list.id}",
        feed_title=reading_list.name,
        query=query,
        total=total,
        params=params,
        order_by=(ReadingListItem.position,),
    )


# 3. SERIES: List Comics (Flattening Volumes)

@router.get("/series/{series_id}", name="series")
async def opds_series(
        series_id: int,
        request: Request,
        user: OPDSUser,
        db: SessionDep,
        params: Annotated[PaginationParams, Depends()]
):

    series = get_authorized_opds_series(series_id, user, db)

    # Fetch comics with RICH metadata
    query = get_visible_opds_comics_query(db, user).filter(Volume.series_id == series.id)

    total = query.count()
    entries = []
    volume_count = db.query(func.count(Volume.id)).filter(Volume.series_id == series.id).scalar() or 0
    if volume_count > 1:
        entries.append({
            "id": f"urn:parker:series:{series.id}:volumes",
            "title": "Browse Volumes",
            "updated": format_opds_datetime(series.updated_at),
            "link": str(request.url_for("opds_series_volumes", series_id=series.id)),
            "summary": f"Browse {series.name} by volume.",
        })

    return render_opds_books_feed(
        request,
        feed_id=f"urn:parker:series:{series.id}",
        feed_title=series.name,
        query=query,
        total=total,
        params=params,
        entries=entries,
        order_by=(Volume.volume_number, Comic.number),
    )


@router.get("/series/{series_id}/volumes", name="opds_series_volumes")
async def opds_series_volumes(
        series_id: int,
        request: Request,
        user: OPDSUser,
        db: SessionDep,
        params: Annotated[PaginationParams, Depends()]
):
    series = get_authorized_opds_series(series_id, user, db)

    query = db.query(Volume).join(Comic).filter(Volume.series_id == series.id)

    age_filter = get_comic_age_restriction(user)
    if age_filter is not None:
        query = query.filter(age_filter)

    query = query.group_by(Volume.id)
    total = query.count()
    volumes = (
        query
        .order_by(Volume.volume_number)
        .offset(params.skip)
        .limit(params.size)
        .all()
    )

    return render_xml(request, {
        "feed_id": f"urn:parker:series:{series.id}:volumes",
        "feed_title": f"{series.name} Volumes",
        "updated_at": format_opds_datetime(datetime.now(timezone.utc)),
        "feed_links": get_opds_pagination_links(request, total, params),
        "entries": get_opds_volume_entries(request, db, volumes, user),
        "books": [],
    })


@router.get("/volumes/{volume_id}", name="opds_volume")
async def opds_volume(
        volume_id: int,
        request: Request,
        user: OPDSUser,
        db: SessionDep,
        params: Annotated[PaginationParams, Depends()]
):
    volume = get_authorized_opds_volume(volume_id, user, db)

    query = get_visible_opds_comics_query(db, user).filter(Comic.volume_id == volume.id)
    total = query.count()

    volume_number = volume.volume_number if volume.volume_number is not None else "Unknown"
    return render_opds_books_feed(
        request,
        feed_id=f"urn:parker:volume:{volume.id}",
        feed_title=f"{volume.series.name} Volume {volume_number}",
        query=query,
        total=total,
        params=params,
        order_by=(Comic.number,),
    )


templates.env.globals["format_opds_datetime"] = format_opds_datetime
templates.env.globals["format_opds_issued"] = format_opds_issued
templates.env.globals["get_opds_acquisition_type"] = get_opds_acquisition_type
templates.env.globals["get_opds_download_href"] = get_opds_download_href
templates.env.globals["get_opds_thumbnail_href"] = get_opds_thumbnail_href


# 4. DOWNLOAD: Serve the file
@router.get("/images/{comic_id}/thumbnail.jpg", name="opds_thumbnail")
async def opds_thumbnail(comic_id: int, user: OPDSUser, db: SessionDep):
    comic = get_authorized_opds_comic(comic_id, user, db, hide_denied=True)

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
