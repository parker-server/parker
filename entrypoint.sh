#!/bin/sh
set -e

# Ensure storage directories exist
mkdir -p /app/storage/database \
         /app/storage/cache \
         /app/storage/cover \
         /app/storage/logs \
         /app/storage/avatars

# Run migrations
alembic upgrade head

# Compact the database if needed, then initialize default settings
python - << 'EOF'
import logging

from app.config import settings
from app.database import SessionLocal
from app.services.admin_bootstrap import ensure_initial_admin
from app.services.backup import BackupService
from app.services.settings_service import SettingsService

logging.basicConfig(level=logging.INFO, format="%(levelname)-5.5s [%(name)s] %(message)s")

# Runs before the server starts, so nothing else is using the database.
BackupService.compact_database_if_needed()

db = SessionLocal()
try:
    ensure_initial_admin(db, app_settings=settings)
    SettingsService(db).initialize_defaults()
finally:
    db.close()
EOF

# Start Uvicorn
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
