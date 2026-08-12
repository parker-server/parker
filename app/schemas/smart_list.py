from typing import Optional
from pydantic import BaseModel, ConfigDict

from app.schemas.search import SearchRequest

class SmartListCreate(BaseModel):
    name: str
    query: SearchRequest  # Re-use your existing schema to validate the JSON

class SmartListUpdate(BaseModel):
    name: Optional[str] = None
    icon: Optional[str] = None
    show_on_dashboard: Optional[bool] = None
    show_in_library: Optional[bool] = None
    query: Optional[SearchRequest] = None

class SmartListResponse(BaseModel):
    id: int
    name: str
    icon: str
    show_on_dashboard: bool
    query: SearchRequest

    # We generally don't send the full complex query_config unless needed

    model_config = ConfigDict(from_attributes=True)