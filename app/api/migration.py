from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

from app.api.deps import AdminUser, SessionDep
from app.services.kavita_migration import KavitaMigrationService
from app.services.kavita_migration_staging import (
    consume_preview,
    discard_preview,
    remove_staged_database,
    stage_kavita_database,
    store_preview,
)


router = APIRouter()


def _validate_user_strategy(user_strategy: str) -> None:
    if user_strategy != "temp-password":
        raise HTTPException(status_code=400, detail="Only 'temp-password' strategy is currently supported.")


@router.post("/preview", name="kavita_migration_preview")
def preview_kavita_migration(
    db: SessionDep,
    admin: AdminUser,
    kavita_db_file: UploadFile = File(..., description="Kavita SQLite database file"),
    user_strategy: Annotated[str, Form(description="User migration strategy")] = "temp-password",
):
    """Stage a Kavita database and return a read-only migration preview."""
    _validate_user_strategy(user_strategy)

    temp_file_path = stage_kavita_database(kavita_db_file.file)
    service = None
    preview_stored = False

    try:
        service = KavitaMigrationService(db=db, kavita_db_path=temp_file_path)
        preview = service.preview_migration()
        preview_token, expires_at = store_preview(admin.id, temp_file_path)
        preview_stored = True

        return {
            **preview,
            "preview_token": preview_token,
            "expires_at": expires_at.isoformat(),
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"File error: {exc}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Preview failed due to a critical error: {exc}") from exc
    finally:
        if service:
            service.close()
        if not preview_stored:
            remove_staged_database(temp_file_path)


@router.post("/preview/discard", name="kavita_migration_discard_preview")
def discard_kavita_migration_preview(
    admin: AdminUser,
    preview_token: Annotated[str, Form(description="Migration preview token")],
):
    """Discard a staged migration preview when its selected database changes."""
    if not discard_preview(preview_token, admin.id):
        raise HTTPException(status_code=404, detail="Migration preview not found.")

    return {"message": "Migration preview discarded."}


@router.post("/run", name="kavita_migration")
def run_kavita_migration(
    db: SessionDep,
    admin: AdminUser,
    preview_token: Annotated[str, Form(description="Migration preview token")],
    user_strategy: Annotated[str, Form(description="User migration strategy")] = "temp-password",
):
    """Run a previously previewed Kavita migration (users + reading progress)."""
    _validate_user_strategy(user_strategy)

    staged_preview = consume_preview(preview_token, admin.id)
    if staged_preview is None:
        raise HTTPException(
            status_code=409,
            detail="Migration preview is no longer available. Upload and preview the Kavita database again.",
        )

    service = None

    try:
        try:
            service = KavitaMigrationService(db=db, kavita_db_path=staged_preview.db_path)
            csv_data = service.migrate_users(strategy=user_strategy)
            stats = service.migrate_progress()
            db.commit()

        except Exception:
            db.rollback()
            raise

        finally:
            if service:
                service.close()

        if user_strategy == "temp-password" and csv_data:
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
        remove_staged_database(staged_preview.db_path)
