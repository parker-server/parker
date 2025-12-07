from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.templates import templates

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    """Admin Dashboard / Hub"""
    return templates.TemplateResponse(request=request, name="admin/dashboard.html")

@router.get("/jobs", response_class=HTMLResponse)
async def admin_jobs_page(request: Request):
    """Serve the Admin Job History page"""
    return templates.TemplateResponse(request=request, name="admin/jobs.html")

@router.get("/users", response_class=HTMLResponse)
async def admin_users_page(request: Request):
    """Serve the Admin User Management page"""
    return templates.TemplateResponse(request=request, name="admin/users.html")

@router.get("/libraries", response_class=HTMLResponse)
async def admin_libraries_page(request: Request):
    """Serve the Admin library Management page"""
    return templates.TemplateResponse(request=request, name="admin/libraries.html")

@router.get("/tasks", response_class=HTMLResponse)
async def admin_tasks_page(request: Request):
    """Serve the Admin Tasks page"""
    return templates.TemplateResponse(request=request, name="admin/tasks.html")

@router.get("/stats", response_class=HTMLResponse)
async def admin_stats_page(request: Request):
    """Serve the Admin Statistics page"""
    return templates.TemplateResponse(request=request, name="admin/stats.html")

@router.get("/settings", response_class=HTMLResponse)
async def admin_settings_page(request: Request):
    """Serve the Admin Settings page"""
    return templates.TemplateResponse(request=request, name="admin/settings.html")

@router.get("/reports", response_class=HTMLResponse)
async def admin_reports_index_page(request: Request):
    """Serve the Admin index report page"""
    return templates.TemplateResponse(request=request, name="admin/reports/index.html")

@router.get("/reports/missing", response_class=HTMLResponse)
async def admin_reports_missing_page(request: Request):
    """Serve the Admin Missing report page"""
    return templates.TemplateResponse(request=request, name="admin/reports/missing.html")

@router.get("/reports/storage", response_class=HTMLResponse)
async def admin_reports_storage_page(request: Request):
    """Serve the Admin Storage report page"""
    return templates.TemplateResponse(request=request, name="admin/reports/storage.html")

@router.get("/reports/metadata", response_class=HTMLResponse)
async def admin_reports_metadata_page(request: Request):
    """Serve the Admin Metadata report page"""
    return templates.TemplateResponse(request=request, name="admin/reports/metadata.html")

@router.get("/reports/duplicates", response_class=HTMLResponse)
async def admin_reports_duplicates_page(request: Request):
    """Serve the Admin Duplicates report page"""
    return templates.TemplateResponse(request=request, name="admin/reports/duplicates.html")

@router.get("/reports/corrupt", response_class=HTMLResponse)
async def admin_reports_corrupt_page(request: Request):
    """Serve the Admin Duplicates report page"""
    return templates.TemplateResponse(request=request, name="admin/reports/corrupt.html")

@router.get("/migration", response_class=HTMLResponse)
async def admin_migration_page(request: Request):
    """Serve the Admin migration page"""
    return templates.TemplateResponse(request=request, name="admin/migration.html")

