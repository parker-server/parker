from pydantic import BaseModel, ConfigDict
from typing import Optional, Any, List

# --- Schemas ---
class PullListCreate(BaseModel):
    name: str
    description: Optional[str] = None

class PullListUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None

class AddComicRequest(BaseModel):
    comic_id: int

class ReorderRequest(BaseModel):
    # Accepts a list of Comic IDs in the new desired order
    comic_ids: List[int]