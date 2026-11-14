from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, List, Any, Optional
from app.api.deps import SessionDep, AdminUser, CurrentUser, get_current_user_optional
from app.core import setting_keys
from app.services.settings_service import SettingsService, SettingValidationError
from app.core.settings_loader import get_cached_setting
from app.services.scheduler import scheduler_service
from app.schemas.setting import SettingUpdate, SettingResponse
from app.models.user import User

router = APIRouter()

PUBLIC_KEYS = [ setting_keys.UI_BACKGROUND_STYLE, setting_keys.UI_PAGINATION_MODE, setting_keys.UI_LOGIN_BACKGROUND_STYLE, setting_keys.UI_LOGIN_SOLID_COLOR, setting_keys.UI_LOGIN_STATIC_COVER ]

@router.get("/", response_model=Dict[str, List[SettingResponse]], status_code=200, name="list")
def get_settings(db: SessionDep):
    """Get all settings grouped by category"""
    svc = SettingsService(db)
    return svc.get_all_grouped()

@router.get("/{key}", name="value")
def get_setting_value(key: str, db: SessionDep, current_user: Optional[User] = Depends(get_current_user_optional)):
    # OPTIMIZATION: Use an optional dependency.
    # This prevents a mandatory DB lookup/401 error for public keys.

    # 1. Fast Path: Public Keys (No Auth, No DB if cached)
    if key in PUBLIC_KEYS:
        # Use the cache helper to avoid a DB hit for static UI settings
        val = get_cached_setting(key)

        # If not in cache, fallback to service (optional safety)
        if val is None:
            svc = SettingsService(db)
            val = svc.get(key)

        return {"value": val}

    # 2. Slow Path: Protected Keys (Requires Auth)
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if not current_user.is_superuser:
        return None  # Or 403?

    svc = SettingsService(db)
    return {"value": svc.get(key)}

@router.patch("/{key}", tags=["admin"], name="update")
def update_setting(key: str, payload: SettingUpdate, db: SessionDep, admin: AdminUser):
    """Update a specific setting"""
    svc = SettingsService(db)
    try:
        setting = svc.update(key, payload.value)

        # Any scheduled task's interval, such as system.task.backup.interval
        if setting_keys.is_task_interval(key):
            scheduler_service.reschedule_jobs()

        return setting

    except SettingValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError:
        raise HTTPException(status_code=404, detail="Setting not found")
