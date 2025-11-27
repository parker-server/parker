from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, FileResponse
from sqlalchemy.orm import Session
from typing import List
from pathlib import Path

from app.api.deps import SessionDep, CurrentUser
from app.models.comic import Comic, Volume
from app.models.series import Series

from app.schemas.search import SearchRequest, SearchResponse
from app.services.search import SearchService
from app.services.images import ImageService

router = APIRouter()


@router.get("/")
async def list_comics(db: SessionDep, current_user: CurrentUser):
    """List all comics"""
    comics = db.query(Comic).join(Volume).join(Series).all()

    result = []
    for comic in comics:
        result.append({
            "id": comic.id,
            "filename": comic.filename,
            "series": comic.volume.series.name,
            "volume": comic.volume.volume_number,
            "number": comic.number,
            "title": comic.title,
            "page_count": comic.page_count,
            "year": comic.year
        })

    return {
        "total": len(result),
        "comics": result
    }


@router.post("/search", response_model=SearchResponse)
async def search_comics(request: SearchRequest, db: SessionDep, current_user: CurrentUser):
    """
    Search comics with complex filters

    Example request:
```json
    {
      "match": "all",
      "filters": [
        {"field": "character", "operator": "contains", "value": ["Batman", "Superman"]},
        {"field": "year", "operator": "equal", "value": 1985},
        {"field": "publisher", "operator": "equal", "value": "DC Comics"}
      ],
      "sort_by": "year",
      "sort_order": "desc",
      "limit": 50
    }
```
    """
    search_service = SearchService(db)
    results = search_service.search(request)
    return results

@router.get("/{comic_id}")
async def get_comic(comic_id: int, db: SessionDep, current_user: CurrentUser):
    """Get a specific comic with all metadata"""
    comic = db.query(Comic).filter(Comic.id == comic_id).first()

    if not comic:
        raise HTTPException(status_code=404, detail="Comic not found")

    # Build credits dictionary by role
    credits = {}
    for credit in comic.credits:
        if credit.role not in credits:
            credits[credit.role] = []
        credits[credit.role].append(credit.person.name)

    return {
        "id": comic.id,
        "filename": comic.filename,
        "file_path": comic.file_path,
        "file_size": comic.file_size,
        "thumbnail_path": comic.thumbnail_path,

        # Series info
        "series_id": comic.volume.series.id,
        "series": comic.volume.series.name,
        "volume": comic.volume.volume_number,
        "number": comic.number,
        "title": comic.title,
        "summary": comic.summary,
        "web": comic.web,
        "notes": comic.notes,

        # Date
        "year": comic.year,
        "month": comic.month,
        "day": comic.day,

        # Credits (grouped by role)
        "credits": credits,

        # Or if you prefer individual fields:
        #"writer": credits.get('writer', []),
        #"penciller": credits.get('penciller', []),
        #"inker": credits.get('inker', []),
        #"colorist": credits.get('colorist', []),
        #"letterer": credits.get('letterer', []),
        #"cover_artist": credits.get('cover_artist', []),
        #"editor": credits.get('editor', []),

        # Publishing
        "publisher": comic.publisher,
        "imprint": comic.imprint,
        "format": comic.format,
        "series_group": comic.series_group,

        # Technical
        "page_count": comic.page_count,
        "scan_information": comic.scan_information,

        # Tags (now from relationships)
        "characters": [c.name for c in comic.characters],
        "teams": [t.name for t in comic.teams],
        "locations": [l.name for l in comic.locations],

        # Reading lists
        "alternate_series": comic.alternate_series,
        "alternate_number": comic.alternate_number,
        "story_arc": comic.story_arc,

        # Timestamps
        "created_at": comic.created_at,
        "updated_at": comic.updated_at
    }


@router.get("/{comic_id}/pages")
async def get_comic_pages(comic_id: int, db: SessionDep, current_user: CurrentUser):
    """Get list of all pages in a comic"""
    comic = db.query(Comic).filter(Comic.id == comic_id).first()

    if not comic:
        raise HTTPException(status_code=404, detail="Comic not found")

    image_service = ImageService()
    page_count = image_service.get_page_count(comic.file_path)

    return {
        "comic_id": comic.id,
        "page_count": page_count,
        "pages": [
            {
                "index": i,
                "url": f"/comics/{comic_id}/page/{i}"
            }
            for i in range(page_count)
        ]
    }


@router.get("/{comic_id}/page/{page_index}")
async def get_comic_page(
        comic_id: int,
        page_index: int,
        db: SessionDep
):
    """
    Get a specific page image from a comic

    Args:
        comic_id: ID of the comic
        page_index: Zero-based page index (0 = first page/cover)
    """
    comic = db.query(Comic).filter(Comic.id == comic_id).first()

    if not comic:
        raise HTTPException(status_code=404, detail="Comic not found")

    image_service = ImageService()
    image_bytes = image_service.get_page_image(comic.file_path, page_index)

    if not image_bytes:
        raise HTTPException(status_code=404, detail="Page not found")

    # Detect image type from bytes
    if image_bytes.startswith(b'\xff\xd8\xff'):
        media_type = "image/jpeg"
    elif image_bytes.startswith(b'\x89PNG'):
        media_type = "image/png"
    elif image_bytes.startswith(b'GIF'):
        media_type = "image/gif"
    elif image_bytes.startswith(b'RIFF') and b'WEBP' in image_bytes[:20]:
        media_type = "image/webp"
    else:
        media_type = "image/jpeg"  # Default

    return Response(
        content=image_bytes,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=31536000",  # Cache for 1 year
            "Content-Disposition": f'inline; filename="page_{page_index}.jpg"'
        }
    )

@router.get("/{comic_id}/cover")
async def get_comic_cover(comic_id: int, db: SessionDep):
    """Get the cover image (first page) of a comic"""
    return await get_comic_page(comic_id, 0, db)


@router.get("/{comic_id}/thumbnail")
async def get_comic_thumbnail(
        comic_id: int,
        db: SessionDep
):
    """
        Get the thumbnail for a comic.
        Serves from storage/cover. Regenerates if missing.
        Self-healing: Generates file if missing, but DOES NOT write to DB
        to avoid locking issues during parallel loading.
    """

    # 1. We don't strictly need to query the comic if we trust the ID,
    # but it's good to ensure the ID is valid.
    comic = db.query(Comic).filter(Comic.id == comic_id).first()
    if not comic:
        raise HTTPException(status_code=404, detail="Comic not found")

    # 2. Layer 1: Check the path stored in the Database
    if comic.thumbnail_path:
        db_path = Path(comic.thumbnail_path)
        if db_path.exists():
            return FileResponse(db_path, media_type="image/webp")

    # 3. Layer 2: Check the "Standard" path (Self-Healing fallback)
    # This handles cases where the DB is NULL or points to a file that was deleted.
    standard_path = Path(f"./storage/cover/comic_{comic.id}.webp")

    if standard_path.exists():
        return FileResponse(standard_path, media_type="image/webp")

    # 4. Layer 3: Generate on the fly
    # We use the standard path for the new file.
    image_service = ImageService()
    success = image_service.generate_thumbnail(comic.file_path, standard_path)

    if not success:
        # Return a placeholder or 404
        raise HTTPException(status_code=404, detail="Could not generate thumbnail")

    # NOTE: We serve the file, but we DO NOT write back to the DB here.
    # This avoids the "Database Locked" issues during parallel loading.
    # The next time this runs, it will hit Layer 2 and succeed.
    return FileResponse(standard_path, media_type="image/webp")
