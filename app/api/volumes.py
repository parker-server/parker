from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from typing import List
from pathlib import Path

from app.database import get_db
from app.models.comic import Comic, Volume
from app.models.series import Series

router = APIRouter()