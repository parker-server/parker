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

@router.get("/library/{library_id}")
async def list_series_for_library(library_id: int, db: Session = Depends(get_db)):
    """List all series for a library"""
    series = db.query(Series).filter(Series.library_id == library_id).all()

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