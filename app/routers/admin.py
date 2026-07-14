import platform
import sys
import os

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse

from app.api.deps import AdminUser, SessionDep
from app.config import settings
from app.core.templates import templates
from app.services.startup_diagnostics import build_support_snapshot, collect_startup_diagnostics

router = APIRouter()

@router.get("/", response_class=HTMLResponse, name="dashboard", tags=['admin'])
async def admin_dashboard(request: Request, admin_user: AdminUser):
    """Admin Dashboard / Hub"""
    return templates.TemplateResponse(request=request, name="admin/dashboard.html")

@router.get("/jobs", response_class=HTMLResponse, name="jobs", tags=['admin'])
async def admin_jobs_page(request: Request, admin_user: AdminUser):
    """Serve the Admin Job History page"""
    return templates.TemplateResponse(request=request, name="admin/jobs.html")

@router.get("/users", response_class=HTMLResponse, name="users", tags=['admin'])
async def admin_users_page(request: Request, admin_user: AdminUser):
    """Serve the Admin User Management page"""
    return templates.TemplateResponse(request=request, name="admin/users.html")

@router.get("/libraries", response_class=HTMLResponse, name="libraries", tags=['admin'])
async def admin_libraries_page(request: Request, admin_user: AdminUser):
    """Serve the Admin library Management page"""
    return templates.TemplateResponse(request=request, name="admin/libraries.html")

@router.get("/tasks", response_class=HTMLResponse, name="tasks", tags=['admin'])
async def admin_tasks_page(request: Request, admin_user: AdminUser):
    """Serve the Admin Tasks page"""
    return templates.TemplateResponse(request=request, name="admin/tasks.html")

@router.get("/stats", response_class=HTMLResponse, name="stats", tags=['admin'])
async def admin_stats_page(request: Request, admin_user: AdminUser):
    """Serve the Admin Statistics page"""
    return templates.TemplateResponse(request=request, name="admin/stats.html")

@router.get("/settings", response_class=HTMLResponse, name="settings", tags=['admin'])
async def admin_settings_page(request: Request, admin_user: AdminUser):
    """Serve the Admin Settings page"""
    return templates.TemplateResponse(request=request, name="admin/settings.html")

@router.get("/reports", response_class=HTMLResponse, name="reports", tags=['admin'])
async def admin_reports_index_page(request: Request, admin_user: AdminUser):
    """Serve the Admin index report page"""
    return templates.TemplateResponse(request=request, name="admin/reports/index.html")

@router.get("/reports/missing", response_class=HTMLResponse, name="reports_missing", tags=['admin'])
async def admin_reports_missing_page(request: Request, admin_user: AdminUser):
    """Serve the Admin Missing report page"""
    return templates.TemplateResponse(request=request, name="admin/reports/missing.html")

@router.get("/reports/storage", response_class=HTMLResponse, name="reports_storage", tags=['admin'])
async def admin_reports_storage_page(request: Request, admin_user: AdminUser):
    """Serve the Admin Storage report page"""
    return templates.TemplateResponse(request=request, name="admin/reports/storage.html")

@router.get("/reports/metadata", response_class=HTMLResponse, name="reports_metadata", tags=['admin'])
async def admin_reports_metadata_page(request: Request, admin_user: AdminUser):
    """Serve the Admin Metadata report page"""
    return templates.TemplateResponse(request=request, name="admin/reports/metadata.html")

@router.get("/reports/duplicates", response_class=HTMLResponse, name="reports_duplicates", tags=['admin'])
async def admin_reports_duplicates_page(request: Request, admin_user: AdminUser):
    """Serve the Admin Duplicates report page"""
    return templates.TemplateResponse(request=request, name="admin/reports/duplicates.html")

@router.get("/reports/corrupt", response_class=HTMLResponse, name="reports_corrupt", tags=['admin'])
async def admin_reports_corrupt_page(request: Request, admin_user: AdminUser):
    """Serve the Admin Duplicates report page"""
    return templates.TemplateResponse(request=request, name="admin/reports/corrupt.html")

@router.get("/migration", response_class=HTMLResponse, name="migration", tags=['admin'])
async def admin_migration_page(request: Request, admin_user: AdminUser):
    """Serve the Admin migration page"""
    return templates.TemplateResponse(request=request, name="admin/migration.html")


@router.get("/about", response_class=HTMLResponse, name="about", tags=['admin'])
async def admin_about_page(request: Request, admin_user: AdminUser):
    """Serve the Admin About page"""

    context = {
        "app_version": settings.version,
        "python_version": sys.version.split()[0],  # Get just the number (3.11.2)
        "platform": platform.system(),
        "platform_release": platform.release(),
        "architecture": platform.machine(),
        "github_url": "https://github.com/parker-server/parker"  # Or your actual repo
    }

    return templates.TemplateResponse(
        request=request,
        name="admin/about.html",
        context=context
    )


@router.get("/diagnostics", response_class=HTMLResponse, name="diagnostics", tags=['admin'])
async def admin_diagnostics_page(request: Request, db: SessionDep, admin_user: AdminUser):
    """Serve the Admin diagnostics page."""
    diagnostics = collect_startup_diagnostics(
        db,
        database_url=settings.database_url,
    )
    support_snapshot = build_support_snapshot(
        diagnostics,
        app_version=settings.version,
    )

    return templates.TemplateResponse(
        request=request,
        name="admin/diagnostics.html",
        context={
            "diagnostics": diagnostics,
            "support_snapshot": support_snapshot,
        },
    )


@router.get("/logs/download", name="download_logs", tags=['admin'])
async def download_latest_log(admin_user: AdminUser):
    """
    Finds the most recent .log file in the log directory and serves it.
    """
    log_dir = settings.log_dir

    if not log_dir.exists():
        raise HTTPException(status_code=404, detail="Log directory not found")

    # Get all .log files
    files = list(log_dir.glob("*.log"))

    if not files:
        raise HTTPException(status_code=404, detail="No log files found")

    # Sort by modification time (Newest last)
    latest_log = max(files, key=os.path.getmtime)

    return FileResponse(
        path=latest_log,
        filename=latest_log.name,
        media_type='text/plain'
    )
