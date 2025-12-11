from sqlalchemy.orm import Session
from app.models.setting import SystemSetting
from typing import Any, List, Dict

from app.api.deps import SessionDep
from app.core.settings_loader import invalidate_settings_cache

class SettingsService:
    def __init__(self, db: SessionDep):
        self.db = db

    # --- DEFINITIONS ---
    # Define defaults here. The app will ensure these exist on startup.
    DEFAULTS = [
        {
            "key": "general.app_name", "value": "Parker Comic Server",
            "description": "Add a prefix to the server name",
            "category": "general", "data_type": "string",
            "label": "Application Name"
        },
        {
            "key": "scanning.batch_window", "value": "300",
            "category": "scanning", "data_type": "int",
            "label": "Scan Batch Window (Sec)",
            "description": "Time to wait for file operations to settle."
        },
        {
            "key": "ui.background_style", "value": "NONE",
            "category": "appearance", "data_type": "select",
            "label": "Background Style",
            "options": [
                {"label": "No background style", "value": "NONE"},
                {"label": "Hero backdrop style", "value": "HERO"},
                {"label": "Colorscape style (Plex)", "value": "COLORSCAPE"},
                {"label": "Colorscape with Hero overlay", "value": "HYBRID"},
            ]
        },
        {
            "key": "ui.pagination_mode",
            "value": "infinite",
            "category": "appearance",
            "data_type": "select",
            "label": "Pagination Style",
            "description": "How lists of issues are loaded.",
            "options": [
                {"label": "Infinite Scroll (Load on scroll)", "value": "infinite"},
                {"label": "Classic (Page numbers)", "value": "classic"}
            ]
        },
        {
            "key": "ui.on_deck.staleness_weeks", "value": "4",
            "category": "appearance", "data_type": "int",
            "label": "On Deck Staleness (Weeks)",
            "description": "Hide 'Continue Reading' items if not touched for this many weeks. Set to 0 to disable."
        },
        {
            "key": "system.task.backup.interval",
            "value": "weekly",
            "category": "system",
            "data_type": "select",
            "label": "Auto-Backup Interval",
            "description": "How often to perform a full database backup.",
            "options": [
                {"label": "Daily", "value": "daily"},
                {"label": "Weekly", "value": "weekly"},
                {"label": "Monthly", "value": "monthly"},
                {"label": "Disabled", "value": "disabled"}
            ]
        },
        {
            "key": "system.task.cleanup.interval",
            "value": "monthly",
            "category": "system",
            "data_type": "select",
            "label": "Auto-Cleanup Interval",
            "description": "How often to clear orphaned metadata (unused characters, tags, etc).",
            "options": [
                {"label": "Daily", "value": "daily"},
                {"label": "Weekly", "value": "weekly"},
                {"label": "Monthly", "value": "monthly"}
            ]
        },
        {
            "key": "system.parallel_image_processing",
            "value": "false",
            "category": "system",
            "data_type": "bool",
            "label": "Enable Parallel Image Processing",
            "description": "Use all CPU cores to speed up thumbnail generation. May increase system load."
        },
        {
            "key": "system.parallel_image_workers",
            "value": "0",
            "category": "system",
            "data_type": "int",
            "label": "Parallel Image Worker Count",
            "description": "Number of worker processes for thumbnail generation. 0 = auto (use all CPU cores). Values above your CPU count will be clamped."
        },
        {
            "key": "system.task.scan.interval",
            "value": "daily",
            "category": "system",
            "data_type": "select",
            "label": "Scheduled Library Scan",
            "description": "Safety net scan for all libraries (useful if folder watching is unreliable).",
            "options": [
                {"label": "Daily", "value": "daily"},
                {"label": "Weekly", "value": "weekly"},
                {"label": "Disabled", "value": "disabled"}
            ]
        },

        {
            "key": "backup.retention_days", "value": "7",
            "category": "backup", "data_type": "int",
            "label": "Backup Retention (Days)"
        },
        {
            "key": "general.log_level",
            "value": "INFO",
            "category": "general",
            "data_type": "select",
            "label": "Logging Level",
            "options": [
                {"label": "Debug", "value": "DEBUG"},
                {"label": "Info", "value": "INFO"},
                {"label": "Warning", "value": "WARNING"},
                {"label": "Error", "value": "ERROR"},
            ]
        },
        {
            "key": "server.opds_enabled", "value": "false",
            "category": "server", "data_type": "bool",
            "label": "Enable OPDS Feed",
            "description": "Allows external readers (Chunky, Panels) to access library via /opds using Basic Auth."
        },


    ]

    def initialize_defaults(self):
        """Idempotent seed: Only adds keys if they don't exist"""
        existing_keys = {s.key for s in self.db.query(SystemSetting.key).all()}

        for default in self.DEFAULTS:
            if default["key"] not in existing_keys:
                obj = SystemSetting(**default)
                self.db.add(obj)
        self.db.commit()

    def get_all_grouped(self) -> Dict[str, List[SystemSetting]]:
        """Returns settings grouped by category for the UI"""
        settings = self.db.query(SystemSetting).filter(SystemSetting.is_hidden == False).all()
        grouped = {}
        for s in settings:
            s.value = self._cast_value(s.value, s.data_type)  # Cast for API
            if s.category not in grouped:
                grouped[s.category] = []
            grouped[s.category].append(s)
        return grouped

    def get(self, key: str) -> Any:
        """Get a single setting value (Casted)"""
        setting = self.db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if not setting:
            return None
        return self._cast_value(setting.value, setting.data_type)

    def update(self, key: str, value: Any) -> SystemSetting:
        setting = self.db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if not setting:
            raise ValueError("Setting not found")

        # Convert to string for storage
        if setting.data_type == "bool":
            setting.value = str(value).lower()  # "true"/"false"
        else:
            setting.value = str(value)

        self.db.commit()
        self.db.refresh(setting)

        # Clear the read-cache so the app sees the change immediately
        invalidate_settings_cache()

        return setting

    def _cast_value(self, value: str, data_type: str) -> Any:
        """Convert DB String -> Python Type"""
        if value is None: return None
        if data_type == "int":
            return int(value)
        if data_type == "bool":
            return value.lower() in ('true', '1', 't', 'yes')
        return value