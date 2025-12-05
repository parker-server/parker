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
async def admin_jobs_page(request: Request):
    """Serve the Admin User Managegment page"""
    return templates.TemplateResponse(request=request, name="admin/users.html")

@router.get("/libraries", response_class=HTMLResponse)
async def admin_jobs_page(request: Request):
    """Serve the Admin library Managegment page"""
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

