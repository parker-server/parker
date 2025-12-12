from fastapi import APIRouter, Depends

from app.api.deps import SessionDep, AdminUser
from app.services.maintenance import MaintenanceService
from app.services.backup import BackupService
from app.services.scan_manager import scan_manager

router = APIRouter()


@router.post("/cleanup", name="cleanup")
async def run_cleanup_task(
        db: SessionDep,
        admin: AdminUser
):
    """
    Trigger database garbage collection.
    Removes tags, people, and collections that have no associated comics.
    """
    # Offload to Job Queue
    result = scan_manager.add_cleanup_task()

    return result


@router.post("/backup", name="backup")
async def run_backup_task(
        admin: AdminUser
):
    """
    Trigger a database backup immediately.
    """
    result = BackupService.create_backup()

    return {
        "message": "Backup created successfully",
        "details": result
    }

@router.post("/refresh-descriptions", name="refresh_descriptions")
async def run_refresh_descriptions_task(
        db: SessionDep,
        admin: AdminUser
):
    """
    Trigger enrichment of reading list descriptions from the seed file.
    """
    service = MaintenanceService(db)
    stats = service.refresh_reading_list_descriptions()

    return {
        "message": "Enrichment complete",
        "stats": stats
    }

@router.post("/refresh-colorscapes", name="colorscape_refresh")
async def run_colorscape_refresh_task(
        db: SessionDep,
        admin: AdminUser
):
    """
    Generate colors for comics that are missing them.
    """
    service = MaintenanceService(db)
    stats = service.backfill_colors()

    return {
        "message": "ColorScape backfill complete",
        "stats": stats
    }