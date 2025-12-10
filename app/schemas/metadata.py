from pydantic import BaseModel
from typing import Optional

# --- Schema for the Request ---
class MetadataUpdate(BaseModel):
    # Match ComicInfo tags exactly for easier mapping, or map manually
    Title: Optional[str] = None
    Series: Optional[str] = None
    Number: Optional[str] = None
    Summary: Optional[str] = None
    Year: Optional[int] = None
    Writer: Optional[str] = None
    Penciller: Optional[str] = None
    Publisher: Optional[str] = None
    # ... add other fields as needed