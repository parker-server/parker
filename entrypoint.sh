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

# Initialize default settings
python - << 'EOF'
from app.config import settings
from app.database import SessionLocal
from app.services.admin_bootstrap import ensure_initial_admin
from app.services.settings_service import SettingsService

db = SessionLocal()
try:
    ensure_initial_admin(db, app_settings=settings)
    SettingsService(db).initialize_defaults()
finally:
    db.close()
EOF

# Start Uvicorn
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
