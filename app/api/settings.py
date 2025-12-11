from fastapi import APIRouter, HTTPException
from typing import Dict, List, Any
from app.api.deps import SessionDep, AdminUser, CurrentUser
from app.services.settings_service import SettingsService
from app.services.scheduler import scheduler_service
from app.schemas.setting import SettingUpdate, SettingResponse

router = APIRouter()

PUBLIC_KEYS = [ "ui.background_style", "ui.pagination_mode" ]

@router.get("/", response_model=Dict[str, List[SettingResponse]], status_code=200, name="list")
def get_settings(db: SessionDep):
    """Get all settings grouped by category"""
    svc = SettingsService(db)
    return svc.get_all_grouped()

@router.get("/{key}", name="value")
def get_setting_value(key: str, db: SessionDep, current_user: CurrentUser):

    # Return setting value if user is an admin or the key is in allowable public setting keys
    if current_user.is_superuser or key in PUBLIC_KEYS:
        svc = SettingsService(db)
        return { "value": svc.get(key) }

    return None

@router.patch("/{key}", tags=["admin"], name="update")
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