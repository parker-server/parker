from pydantic import BaseModel, ConfigDict
from typing import Optional, Any, List

class SettingBase(BaseModel):
    key: str
    value: Any # We will accept int/bool/str and convert to string for DB
    category: str
    data_type: str
    label: str
    description: Optional[str] = None
    options: Optional[List[Any]] = None
    depends_on: Optional[Any] = None
    min_value: Optional[Any] = None

class SettingUpdate(BaseModel):
    value: Any

class SettingResponse(SettingBase):
    model_config = ConfigDict(from_attributes=True)
