"""
Every system setting key, declared once.

SettingsService.DEFAULTS and the code that reads settings both use these
constants instead of typing keys out. This module imports nothing, so any
module can import it without an import cycle. Each name is its key in upper
case with dots replaced by underscores. Templates cannot import these and
keep string keys; tests check those keys exist.
"""

# General
GENERAL_APP_NAME = "general.app_name"
GENERAL_LOG_LEVEL = "general.log_level"

# Scanning
SCANNING_BATCH_WINDOW = "scanning.batch_window"

# Appearance
UI_LOGIN_BACKGROUND_STYLE = "ui.login_background_style"
UI_LOGIN_SOLID_COLOR = "ui.login_solid_color"
UI_LOGIN_STATIC_COVER = "ui.login_static_cover"
UI_BACKGROUND_STYLE = "ui.background_style"
UI_PAGINATION_MODE = "ui.pagination_mode"
UI_SERIES_ISSUE_VOLUME_DIVIDERS = "ui.series_issue_volume_dividers"
UI_AUTO_REDIRECT_SINGLE_VOLUME_SERIES = "ui.auto_redirect_single_volume_series"
UI_ON_DECK_STALENESS_WEEKS = "ui.on_deck.staleness_weeks"

# System
SYSTEM_TASK_BACKUP_INTERVAL = "system.task.backup.interval"
SYSTEM_TASK_CLEANUP_INTERVAL = "system.task.cleanup.interval"
SYSTEM_TASK_SCAN_INTERVAL = "system.task.scan.interval"
JOBS_RETENTION_DAYS = "jobs.retention_days"
NOTIFICATIONS_RETENTION_DAYS = "notifications.retention_days"
SYSTEM_PARALLEL_METADATA_PROCESSING = "system.parallel_metadata_processing"
SYSTEM_PARALLEL_METADATA_WORKERS = "system.parallel_metadata_workers"
SYSTEM_PARALLEL_METADATA_WRITER_SUMMARY_TIMEOUT_SECONDS = "system.parallel_metadata_writer_summary_timeout_seconds"
SYSTEM_PARALLEL_METADATA_WRITER_JOIN_TIMEOUT_SECONDS = "system.parallel_metadata_writer_join_timeout_seconds"
SYSTEM_PARALLEL_IMAGE_PROCESSING = "system.parallel_image_processing"
SYSTEM_PARALLEL_IMAGE_WORKERS = "system.parallel_image_workers"
SYSTEM_PARALLEL_IMAGE_WRITER_SUMMARY_TIMEOUT_SECONDS = "system.parallel_image_writer_summary_timeout_seconds"
SYSTEM_PARALLEL_IMAGE_WRITER_JOIN_TIMEOUT_SECONDS = "system.parallel_image_writer_join_timeout_seconds"
NOTIFICATIONS_WHATS_NEW_LAST_VERSION = "notifications.whats_new_last_version"
NOTIFICATIONS_YEAR_IN_REVIEW_LAST_YEAR = "notifications.year_in_review_last_year"
NOTIFICATIONS_UPDATE_LAST_VERSION = "notifications.update_last_version"

# Backup
BACKUP_RETENTION_DAYS = "backup.retention_days"

# Server
SERVER_OPDS_ENABLED = "server.opds_enabled"
SERVER_DOWNLOADS_ENABLED = "server.downloads_enabled"
SERVER_UPDATE_CHECK_ENABLED = "server.update_check_enabled"
INTEGRATIONS_EXTERNAL_REVIEWS_ENABLED = "integrations.external_reviews.enabled"

# Features
FEATURES_NOTIFICATIONS_ENABLED = "features.notifications_enabled"
FEATURES_PROBLEM_REPORTS_ENABLED = "features.problem_reports_enabled"


def task_interval(job_id: str) -> str:
    """The interval setting for a scheduled task, such as system.task.backup.interval."""
    return f"system.task.{job_id}.interval"


def is_task_interval(key: str) -> bool:
    return key.startswith("system.task.") and key.endswith(".interval")
