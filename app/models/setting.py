from sqlalchemy import Column, String, Text, Boolean, JSON
from app.database import Base


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String, primary_key=True, index=True)  # e.g., "scan.batch_window"
    value = Column(Text, nullable=True)  # e.g., "300"
    category = Column(String, index=True)  # e.g., "scanning"
    data_type = Column(String)  # "int", "bool", "string", "path", "select"

    # UI Metadata
    label = Column(String)  # "Batch Window (Seconds)"
    description = Column(String, nullable=True)  # "Time to wait before scanning..."
    is_hidden = Column(Boolean, default=False)  # For internal system flags

    # Store valid choices here when applicable
    options = Column(JSON, nullable=True)
    depends_on = Column(JSON, nullable=True) # Used for cascading / linked dropdowns