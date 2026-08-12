import logging
from typing import Protocol

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.models.user import User


logger = logging.getLogger("app.startup")

DEFAULT_INITIAL_ADMIN_USERNAME = "admin"
DEFAULT_INITIAL_ADMIN_EMAIL = "admin@example.com"
INITIAL_ADMIN_PASSWORD_MIN_LENGTH = 8
INSECURE_INITIAL_ADMIN_PASSWORD_VALUES = {
    "admin",
    "changeme",
    "change-me",
    "change_this_to_a_real_password",
    "change_this_to_a_secure_password",
    "letmein",
    "parker",
    "parkeradmin",
    "password",
    "password123",
    "your-password-here",
}


class AdminBootstrapSettings(Protocol):
    initial_admin_username: str
    initial_admin_password: str


class AdminBootstrapError(RuntimeError):
    pass


def _active_superuser_exists(db: Session) -> bool:
    return (
        db.query(User.id)
        .filter(
            User.is_superuser == True,
            User.is_active == True,
        )
        .first()
        is not None
    )


def _normalize_initial_admin_username(username: str) -> str:
    cleaned = username.strip() or DEFAULT_INITIAL_ADMIN_USERNAME
    return cleaned


def _initial_admin_email(username: str) -> str:
    if username == DEFAULT_INITIAL_ADMIN_USERNAME:
        return DEFAULT_INITIAL_ADMIN_EMAIL
    return f"{username}@parker.local"


def _validate_initial_admin_password(password: str) -> str:
    cleaned = password.strip()
    if not cleaned:
        raise AdminBootstrapError(
            "Parker needs to create the first administrator account, but INITIAL_ADMIN_PASSWORD is not set. "
            "Set INITIAL_ADMIN_PASSWORD to a unique password before starting Parker."
        )

    if len(cleaned) < INITIAL_ADMIN_PASSWORD_MIN_LENGTH:
        raise AdminBootstrapError(
            f"INITIAL_ADMIN_PASSWORD must be at least {INITIAL_ADMIN_PASSWORD_MIN_LENGTH} characters long."
        )

    if cleaned.lower() in INSECURE_INITIAL_ADMIN_PASSWORD_VALUES:
        raise AdminBootstrapError(
            "INITIAL_ADMIN_PASSWORD is set to an insecure placeholder value. "
            "Set it to a unique password before starting Parker."
        )

    return cleaned


def ensure_initial_admin(db: Session, *, app_settings: AdminBootstrapSettings) -> User | None:
    """
    Create the first admin only when the database has no active superuser.

    Existing superusers are never modified. This keeps upgrades safe even when
    an existing installation still uses the legacy admin/admin credential.
    """
    if _active_superuser_exists(db):
        if app_settings.initial_admin_password.strip():
            logger.warning(
                "INITIAL_ADMIN_PASSWORD is set, but an active administrator already exists. "
                "Ignoring the bootstrap password."
            )
        return None

    username = _normalize_initial_admin_username(app_settings.initial_admin_username)
    password = _validate_initial_admin_password(app_settings.initial_admin_password)
    email = _initial_admin_email(username)

    username_conflict = (
        db.query(User.id)
        .filter(func.lower(User.username) == username.lower())
        .first()
        is not None
    )
    if username_conflict:
        raise AdminBootstrapError(
            f"Cannot create initial administrator {username!r} because that username already exists. "
            "Set INITIAL_ADMIN_USERNAME to an unused username and restart Parker."
        )

    email_conflict = (
        db.query(User.id)
        .filter(func.lower(User.email) == email.lower())
        .first()
        is not None
    )
    if email_conflict:
        raise AdminBootstrapError(
            f"Cannot create initial administrator {username!r} because bootstrap email {email!r} already exists. "
            "Set INITIAL_ADMIN_USERNAME to an unused username and restart Parker."
        )

    user = User(
        username=username,
        email=email,
        hashed_password=get_password_hash(password),
        is_superuser=True,
        is_active=True,
    )
    db.add(user)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if _active_superuser_exists(db):
            logger.info("Initial administrator was created by another startup worker.")
            return None
        raise AdminBootstrapError(
            "Parker could not create the initial administrator because the configured username or email conflicts "
            "with an existing user."
        ) from exc

    db.refresh(user)
    logger.warning(
        "Created initial administrator account username=%r. Change INITIAL_ADMIN_PASSWORD or remove it after setup; "
        "Parker ignores it once an administrator exists.",
        username,
    )
    return user
