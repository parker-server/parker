from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import Response, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import joinedload
from datetime import datetime, timezone

from app.api.opds_deps import OPDSUser, SessionDep
from app.models.library import Library
from app.models.series import Series
from app.models.comic import Comic, Volume

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(prefix="/opds", tags=["opds"])


# Helper to render XML
def render_xml(request: Request, context: dict):
    return templates.TemplateResponse(
        request=request,
        name="opds/feed.xml",
        context=context,
        media_type="application/atom+xml;charset=utf-8"
    )


# 1. ROOT: List Libraries
@router.get("/")
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
            "updated": datetime.now(timezone.utc).isoformat(),  # Libraries rarely change, using now() is acceptable for root
            "link": f"/opds/libraries/{lib.id}",
            "summary": f"Library containing {len(lib.series)} series."
        })

    return render_xml(request, {
        "feed_id": "urn:parker:root",
        "feed_title": "Parker Library",
        "updated_at": datetime.now(timezone.utc),
        "entries": entries,
        "books": []
    })


# 2. LIBRARY: List Series
@router.get("/libraries/{library_id}")
async def opds_library(library_id: int, request: Request, user: OPDSUser, db: SessionDep):
    # Security check using your existing accessible_libraries logic
    if not user.is_superuser:
        allowed_ids = [l.id for l in user.accessible_libraries]
        if library_id not in allowed_ids:
            raise HTTPException(status_code=404, detail="Library not found")

    library = db.query(Library).filter(Library.id == library_id).first()

    # Fetch series
    series_list = db.query(Series).filter(Series.library_id == library_id).order_by(Series.name).all()

    entries = []
    for s in series_list:
        entries.append({
            "id": f"urn:parker:series:{s.id}",
            "title": f"{s.name} ({s.year})",
            "updated": s.updated_at.isoformat(),
            "link": f"/opds/series/{s.id}",
            "summary": s.description,
            # Reuse your existing thumbnail API, passing the series ID
            # Assuming you have a route like /api/series/{id}/thumbnail
            "thumbnail": f"/api/series/{s.id}/thumbnail"
        })

    return render_xml(request, {
        "feed_id": f"urn:parker:lib:{library_id}",
        "feed_title": library.name,
        "updated_at": datetime.now(timezone.utc),
        "entries": entries,
        "books": []
    })


# 3. SERIES: List Comics (Flattening Volumes)

@router.get("/series/{series_id}")
async def opds_series(series_id: int, request: Request, user: OPDSUser, db: SessionDep):
    # ... (Previous Security Check) ...

    # Fetch comics with RICH metadata
    comics = (
        db.query(Comic)
        .join(Volume)
        .join(Series) # Explicit join for filtering
        .filter(Volume.series_id == series_id)
        .options(
            joinedload(Comic.credits).joinedload("person"), # Load credits + person names
            joinedload(Comic.genres),    # Load Genres
            joinedload(Comic.volume).joinedload(Volume.series) # Load Series Name
        )
        .order_by(Volume.volume_number, Comic.number)
        .all()
    )

    return render_xml(request, {
        "feed_id": f"urn:parker:series:{series_id}",
        "feed_title": comics[0].volume.series.name if comics else "Series",
        "updated_at": datetime.now(timezone.utc),
        "entries": [],
        "books": comics
    })


# 4. DOWNLOAD: Serve the file
@router.get("/download/{comic_id}")
async def opds_download(comic_id: int, user: OPDSUser, db: SessionDep):
    # We duplicate the logic from get_secure_comic here because we need
    # to authenticate via Basic Auth (user argument), not JWT.

    comic = db.query(Comic).join(Volume).join(Series).filter(Comic.id == comic_id).first()

    if not comic:
        raise HTTPException(status_code=404)

    if not user.is_superuser:
        if comic.volume.series.library_id not in [l.id for l in user.accessible_libraries]:
            raise HTTPException(status_code=404)

    # Clean filename for headers (remove non-ascii if necessary, but modern browsers/apps handle utf-8)
    export_name = f"{comic.series_group or 'Comic'} - {comic.title}.cbz"

    return FileResponse(
        path=str(comic.file_path),
        filename=export_name,
        media_type="application/vnd.comicbook+zip",
        headers={"Content-Disposition": f'attachment; filename="{export_name}"'}
    )