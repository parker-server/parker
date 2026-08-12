import os
import tempfile
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

from app.api.deps import AdminUser, SessionDep
from app.services.kavita_migration import KavitaMigrationService

router = APIRouter()


@router.post("/run", name="kavita_migration")
async def run_kavita_migration(
    db: SessionDep,
    admin: AdminUser,
    kavita_db_file: UploadFile = File(..., description="Kavita SQLite database file"),
    user_strategy: Annotated[str, Form(description="User migration strategy")] = "temp-password",
):
    """Run full Kavita to Parker migration (users + reading progress)."""
    if user_strategy != "temp-password":
        raise HTTPException(status_code=400, detail="Only 'temp-password' strategy is currently supported.")

    temp_file_path = None
    service = None

    try:
        # 1. Save uploaded file to a temporary location
        # On Windows, we must ensure this file handle is CLOSED before passing the path to SQLite
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
            file_bytes = await kavita_db_file.read()
            tmp.write(file_bytes)
            temp_file_path = tmp.name

        # 2. Initialize the Migration Service
        try:
            # We pass the active Parker DB session (db) and the path to the temp Kavita DB
            service = KavitaMigrationService(db=db, kavita_db_path=temp_file_path)

            # 3. Migrate Users
            # This returns the REAL CSV string of created credentials
            csv_data = service.migrate_users(strategy=user_strategy)

            # 4. Migrate Progress
            stats = service.migrate_progress()

            # One transaction boundary for the entire migration.
            db.commit()

        except Exception:
            # FAILURE! Undo everything.
            # This ensures we don't create users without delivering the password CSV.
            db.rollback()
            raise  # Re-raise to trigger the outer exception handler

        finally:
            # 5. Cleanup Service connections
            # Ensure SQLite connection is closed even if migration fails
            if service:
                service.close()


        # 6. Handle Response
        # Check the REAL csv_data, not the mock one
        if user_strategy == 'temp-password' and csv_data:
            filename = f"parker_migrated_credentials_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
            return Response(
                content=csv_data,
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )

        return JSONResponse(
            content={
                "status": "Migration of users and reading progress complete.",
                "details": stats,
            },
            status_code=200,
        )

    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"File error: {exc}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Migration failed due to a critical error: {exc}") from exc
    finally:
        # Cleanup the temporary file from disk
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except PermissionError:
                # Best-effort cleanup on Windows.
                pass
