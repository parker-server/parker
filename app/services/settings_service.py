from sqlalchemy.orm import Session
import multiprocessing
from app.models.setting import SystemSetting
from typing import Any, List, Dict

from app.api.deps import SessionDep
from app.core.settings_loader import invalidate_settings_cache
from app.core.login_backgrounds import SOLID_COLORS, STATIC_COVERS


def generate_worker_options():
    """Generate CPU worker options based on available cores"""
    try:
        cpu_count = multiprocessing.cpu_count()
    except Exception:
        cpu_count = 1

    # Option 1: Auto
    options = [
        {"label": "Auto (Safe - 50% Load)", "value": "0"}
    ]

    # Option 2: Explicit counts (1 up to Max)
    for i in range(1, cpu_count + 1):
        label = f"{i} Worker{'s' if i > 1 else ''}"
        if i == cpu_count:
            label += " (Max Performance)"

        options.append({"label": label, "value": str(i)})

    return options

def generate_color_options():
    """Generate color options from SOLID_COLORS dictionary"""

    return [
        {"label": data["name"], "value": key, "group": data.get("group")}
        for key, data in SOLID_COLORS.items()
    ]

def generate_cover_options():
    """Generate cover options from STATIC_COVERS dictionary"""

    return [
        {"label": data["name"], "value": filename}
        for filename, data in STATIC_COVERS.items()
    ]

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
            "key": "scanning.batch_window", "value": "600",
            "category": "scanning", "data_type": "int",
            "label": "Scan Batch Window (Sec)",
            "description": "Time to wait for file operations to settle."
        },
        {
            "key": "ui.login_background_style", "value": "random_covers",
            "category": "appearance", "data_type": "select",
            "label": "Login Background Style",
            "description": "Choose what appears behind the login form.",
            "options": [
                {"label": "None (Gradient only)", "value": "none"},
                {"label": "Random library covers", "value": "random_covers"},
                {"label": "Solid Color", "value": "solid_color"},
                {"label": "Static Cover", "value": "static_cover"}
            ]
        },
        {
            "key": "ui.login_solid_color",
            "value": "superman_classic",
            "category": "appearance",
            "data_type": "select",
            "label": "Login Solid Color",
            "description": "Choose a color gradient.",
            "depends_on": { "key": "ui.login_background_style", "value": "solid_color" },
            "options": generate_color_options()
        },
        {
            "key": "ui.login_static_cover",
            "value": "amazing-fantasy-15.jpg",
            "category": "appearance",
            "data_type": "select",
            "label": "Login Static Cover",
            "description": "Choose an iconic comic cover.",
            "depends_on": { "key": "ui.login_background_style", "value": "static_cover" },
            "options": generate_cover_options()
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
            "description": "How lists of series / issues are loaded.",
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
            "data_type": "select",
            "label": "Parallel Image Worker Count",
            "description": "Control how many CPU cores are used for thumbnail generation.",
            "options": generate_worker_options()
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
            "description": "Server restart required for this change to take effect.",
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
        """
        Seeds default settings and updates metadata (labels, descriptions) for existing ones.
        Does NOT overwrite existing 'values' to preserve user configuration.
        """
        # 1. Fetch all existing settings mapped by key for fast lookup
        existing_settings = {
            s.key: s
            for s in self.db.query(SystemSetting).all()
        }

        for default in self.DEFAULTS:

            key = default["key"]

            if key not in existing_settings:
                # Case 1: New Setting -> Create it fully (including default value)
                obj = SystemSetting(**default)
                self.db.add(obj)
            else:
                # Case 2: Existing Setting -> Sync metadata only
                # We update definitions to match the code, but we generally
                # DO NOT touch 'value' so we don't overwrite user preferences.
                setting = existing_settings[key]

                # Update metadata fields
                setting.label = default.get("label")
                setting.description = default.get("description")
                setting.category = default.get("category")
                setting.data_type = default.get("data_type")

                # Update 'options' if your model supports it (JSON column)
                # This ensures new dropdown choices appear in the UI.
                if "options" in default:
                    setting.options = default["options"]

                if "depends_on" in default:
                    setting.depends_on = default["depends_on"]

                # NOTE: If we strictly needed to force-update a value (e.g. security patch),
                # we would need explicit logic here, but usually we leave .value alone.

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

        # Cast the value back to Python type (bool/int) so the API returns
        # actual JSON booleans, not strings like "false".
        setting.value = self._cast_value(setting.value, setting.data_type)

        return setting

    def _cast_value(self, value: str, data_type: str) -> Any:
        """Convert DB String -> Python Type"""
        if value is None: return None
        if data_type == "int":
            return int(value)
        if data_type == "bool":
            return value.lower() in ('true', '1', 't', 'yes')
        return value
