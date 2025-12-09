from fastapi import APIRouter, HTTPException
from typing import Dict, List, Any
from app.api.deps import SessionDep, AdminUser
from app.services.settings_service import SettingsService
from app.services.scheduler import scheduler_service
from app.schemas.setting import SettingUpdate, SettingResponse

router = APIRouter()

@router.get("/", response_model=Dict[str, List[SettingResponse]], status_code=200)
def get_settings(db: SessionDep, admin: AdminUser):
    """Get all settings grouped by category"""
    svc = SettingsService(db)
    return svc.get_all_grouped()

@router.patch("/{key}", tags=["admin"], name="update_setting")
def update_setting(key: str, payload: SettingUpdate, db: SessionDep, admin: AdminUser):
    """Update a specific setting"""
    svc = SettingsService(db)
    try:
        setting = svc.update(key, payload.value)

        # Dynamic check based on naming convention
        # Captures 'system.task.backup.interval', 'system.task.something.interval', etc.
        if key.startswith("system.task.") and key.endswith(".interval"):
            scheduler_service.reschedule_jobs()

        return setting

    except ValueError:
        raise HTTPException(status_code=404, detail="Setting not found")