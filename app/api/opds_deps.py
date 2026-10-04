import logging
from typing import Annotated
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.api.deps import SessionDep
from app.models.user import User
from app.core.security import verify_password
from app.services.opds_tokens import authenticate_opds_token, mark_opds_token_used
from app.services.settings_service import SettingsService

OPDS_AUTH_CHALLENGE = 'Basic realm="Parker OPDS"'
security = HTTPBasic(realm="Parker OPDS")
logger = logging.getLogger("app.auth")

def get_current_user_opds(
        credentials: Annotated[HTTPBasicCredentials, Depends(security)],
        db: SessionDep,
        request: Request,
) -> User:
    """
    Validates Basic Auth credentials for OPDS clients.
    Also checks if OPDS is globally enabled.
    """
    # 1. Check Global Setting
    settings_service = SettingsService(db)
    if not settings_service.get("server.opds_enabled"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPDS Support is disabled on this server."
        )

    # 2. Check User
    user = db.query(User).filter(User.username == credentials.username).first()

    if not user:
        logger.warning(
            "Authentication failed via OPDS basic auth: username=%r ip=%s path=%s reason=unknown_user",
            credentials.username,
            request.client.host if request.client else "unknown",
            request.url.path,
        )
        # OPDS clients need standard 401 to prompt for password
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": OPDS_AUTH_CHALLENGE},
        )

    # 3. Verify a revocable OPDS key or the account password.
    opds_token = authenticate_opds_token(db, user, credentials.password)
    password_valid = False
    if not opds_token:
        password_valid = verify_password(credentials.password, str(user.hashed_password))

    if not password_valid and not opds_token:
        logger.warning(
            "Authentication failed via OPDS basic auth: username=%r ip=%s path=%s reason=invalid_password",
            credentials.username,
            request.client.host if request.client else "unknown",
            request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": OPDS_AUTH_CHALLENGE},
        )

    if user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password change required",
        )

    if opds_token:
        mark_opds_token_used(db, opds_token)

    return user


# Dependency Alias
OPDSUser = Annotated[User, Depends(get_current_user_opds)]
