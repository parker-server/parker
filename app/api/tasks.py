from fastapi import APIRouter, Depends

from app.api.deps import SessionDep, AdminUser
from app.services.maintenance import MaintenanceService
from app.services.backup import BackupService

router = APIRouter()


@router.post("/cleanup")
async def run_cleanup_task(
        db: SessionDep,
        admin: AdminUser
):
    """
    Trigger database garbage collection.
    Removes tags, people, and collections that have no associated comics.
    """
    service = MaintenanceService(db)
    stats = service.cleanup_orphans()

    return {
        "message": "Cleanup complete",
        "stats": stats
    }


@router.post("/backup")
async def run_backup_task(
        admin: AdminUser
):
    """
    Trigger a database backup immediately.
    """
    service = BackupService()
    result = service.create_backup()

    return {
        "message": "Backup created successfully",
        "details": result
    }