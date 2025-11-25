from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from typing import List
from pathlib import Path

from app.database import get_db
from app.models.comic import Comic, Volume
from app.models.series import Series

router = APIRouter()

@router.get("/")
async def list_series(db: Session = Depends(get_db)):
    """List all series"""
    series = db.query(Series).all()

    result = []
    for s in series:
        result.append({
            "id": s.id,
            "name": s.name,
            "library_id": s.library_id
        })

    return {
        "total": len(result),
        "series": result
    }

@router.get("/{series_id}")
async def get_series(series_id: int, db: Session = Depends(get_db)):
    """Get a specific series"""
    series = db.query(Series).filter(Series.id == series_id).first()

    if not series:
        raise HTTPException(status_code=404, detail="Series not found")

    return series

@router.get("/{series_id}/volumes/")
async def list_volumes_for_series(series_id: int, db: Session = Depends(get_db)):
    """List all volumes for a series"""
    volumes = db.query(Volume).filter(Volume.series_id == series_id).all()

    result = []
    for v in volumes:
        result.append({
            "id": v.id,
            "series_id": v.series_id,
            "volume_number": v.volume_number
        })

    return {
        "total": len(result),
        "volumes": result
    }