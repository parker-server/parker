from pydantic import BaseModel, field_validator
from typing import Optional

# --- Schema for the Request ---
class MetadataUpdate(BaseModel):
    # Match ComicInfo tags exactly for easier mapping, or map manually
    Title: Optional[str] = None
    Series: Optional[str] = None
    Number: Optional[str] = None
    Volume: Optional[str] = None
    Summary: Optional[str] = None
    Year: Optional[int] = None
    Writer: Optional[str] = None
    Penciller: Optional[str] = None
    Publisher: Optional[str] = None
    Imprint: Optional[str] = None
    Count: Optional[int] = None
    AlternateSeries: Optional[str] = None
    AlternateNumber: Optional[str] = None
    Genre: Optional[str] = None
    Format: Optional[str] = None
    AgeRating: Optional[str] = None
    # ... add other fields as needed

    @field_validator("Year", "Count", mode="before")
    @classmethod
    def blank_numeric_values_clear_tag(cls, value):
        if value == "":
            return None
        return value
