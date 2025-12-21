from fastapi import APIRouter, Depends

from app.api.deps import SessionDep, AdminUser
from app.models.library import Library
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
    Queue background jobs to generate colors for comics that are missing them.
    """

    libraries = db.query(Library).all()
    queued_count = 0

    for lib in libraries:
        # We call the method on scan_manager
        # force=False ensures we only target items MISSING data (Backfill)
        res = scan_manager.add_thumbnail_task(lib.id, force=False)
        if res['status'] == 'queued':
            queued_count += 1

    return {
        "message": f"Queued background processing for {queued_count} libraries.",
        "stats": {"libraries_queued": queued_count}
    }
