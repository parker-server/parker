from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import AdminUser, SessionDep
from app.services.cbl_catalog_service import (
    CBLCatalogError,
    CBLCatalogNotFoundError,
    CBLCatalogService,
    CBLCatalogUpstreamError,
)
from app.services.cbl_source_service import CBLSourceError, CBLSourceService

router = APIRouter()


class CBLCatalogImportRequest(BaseModel):
    path: str


@router.get("/browse", name="browse")
async def browse_catalog(db: SessionDep, admin: AdminUser, path: str = "", force_refresh: bool = False):
    service = CBLCatalogService(db)

    try:
        return await service.browse(path, force_refresh=force_refresh)
    except CBLCatalogNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CBLCatalogUpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/preview", name="preview")
async def preview_catalog_file(db: SessionDep, admin: AdminUser, path: str):
    service = CBLCatalogService(db)

    try:
        return await service.preview(path)
    except CBLCatalogNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CBLCatalogUpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except CBLCatalogError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/import", name="import_file")
async def import_catalog_file(payload: CBLCatalogImportRequest, db: SessionDep, admin: AdminUser):
    service = CBLCatalogService(db)

    try:
        source = await service.import_file(payload.path)
        CBLSourceService(db).rebuild(source.id)
        db.commit()
    except CBLCatalogNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CBLCatalogUpstreamError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (CBLCatalogError, CBLSourceError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.refresh(source)
    return {
        "id": source.id,
        "display_name": source.display_name,
        "origin": source.origin,
        "catalog_provider": source.catalog_provider,
        "catalog_path": source.catalog_path,
        "entry_count": source.entry_count,
    }
