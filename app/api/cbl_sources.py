from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, SessionDep
from app.models.cbl_source import CBLSource
from app.models.reading_list import ReadingList
from app.services.cbl_catalog_service import CBLCatalogService
from app.services.cbl_source_service import CBLSourceError, CBLSourceService

router = APIRouter()


class CBLSourceUrlRequest(BaseModel):
    url: str


def _serialize(source: CBLSource, reading_list: ReadingList | None = None) -> dict:
    return {
        "id": source.id,
        "display_name": source.display_name,
        "original_filename": source.original_filename,
        "origin": source.origin,
        "source_url": source.source_url,
        "catalog_provider": source.catalog_provider,
        "catalog_path": source.catalog_path,
        "imported_at": source.imported_at,
        "last_refreshed_at": source.last_refreshed_at,
        "last_refresh_status": source.last_refresh_status,
        "entry_count": source.entry_count,
        "last_match_summary": source.last_match_summary,
        "reading_list_id": reading_list.id if reading_list else None,
        "reading_list_name": reading_list.name if reading_list else None,
    }


def _reading_list_for(db: Session, source: CBLSource) -> ReadingList | None:
    return db.query(ReadingList).filter(ReadingList.source_cbl_id == source.id).first()


@router.get("/", name="list")
async def list_cbl_sources(db: SessionDep, admin: AdminUser):
    sources = db.query(CBLSource).order_by(CBLSource.imported_at.desc()).all()

    reading_lists_by_source_id = {}
    if sources:
        reading_lists = db.query(ReadingList).filter(
            ReadingList.source_cbl_id.in_([s.id for s in sources])
        ).all()
        reading_lists_by_source_id = {rl.source_cbl_id: rl for rl in reading_lists}

    return {"items": [_serialize(s, reading_lists_by_source_id.get(s.id)) for s in sources]}


@router.get("/{source_id}", name="detail")
async def get_cbl_source(source_id: int, db: SessionDep, admin: AdminUser):
    source = db.get(CBLSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="CBL source not found")
    return _serialize(source, _reading_list_for(db, source))


@router.post("/upload", name="upload")
async def upload_cbl_source(db: SessionDep, admin: AdminUser, file: UploadFile = File(...)):
    content = await file.read()
    service = CBLSourceService(db)

    try:
        source = service.import_upload(content, file.filename or "upload.cbl")
        service.rebuild(source.id)
        db.commit()
    except CBLSourceError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.refresh(source)
    return _serialize(source, _reading_list_for(db, source))


@router.post("/url", name="import_url")
async def import_cbl_source_from_url(payload: CBLSourceUrlRequest, db: SessionDep, admin: AdminUser):
    service = CBLSourceService(db)

    try:
        source = await service.import_url(payload.url)
        service.rebuild(source.id)
        db.commit()
    except CBLSourceError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.refresh(source)
    return _serialize(source, _reading_list_for(db, source))


@router.post("/{source_id}/refresh", name="refresh")
async def refresh_cbl_source(source_id: int, db: SessionDep, admin: AdminUser):
    source = db.get(CBLSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail=f"CBL source {source_id} not found")

    service = CBLSourceService(db)

    try:
        if source.origin == "catalog":
            source = await CBLCatalogService(db).refresh_source(source_id)
        else:
            source = await service.refresh(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CBLSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if source.last_refresh_status == "ok":
        service.rebuild(source.id)

    db.commit()
    db.refresh(source)
    return _serialize(source, _reading_list_for(db, source))


@router.delete("/{source_id}", name="delete")
async def delete_cbl_source(source_id: int, db: SessionDep, admin: AdminUser):
    service = CBLSourceService(db)

    try:
        service.delete(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"message": "CBL source deleted"}
