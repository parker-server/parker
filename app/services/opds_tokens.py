import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.security import get_password_hash, verify_password
from app.models.opds_token import UserOPDSToken
from app.models.user import User


OPDS_TOKEN_PREFIX = "pk_opds_"
OPDS_TOKEN_BYTES = 32
MAX_OPDS_TOKEN_NAME_LENGTH = 80
OPDS_TOKEN_USED_UPDATE_INTERVAL = timedelta(hours=1)


def generate_opds_token() -> str:
    return f"{OPDS_TOKEN_PREFIX}{secrets.token_urlsafe(OPDS_TOKEN_BYTES)}"


def get_opds_token_hint(token: str) -> str:
    if len(token) <= 16:
        return token
    return f"{token[:12]}...{token[-4:]}"


def normalize_opds_token_name(name: str) -> str:
    normalized = " ".join(name.split()).strip()
    return normalized[:MAX_OPDS_TOKEN_NAME_LENGTH] or "OPDS key"


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def active_opds_token_query(db: Session, user: User, now: datetime | None = None):
    if now is None:
        now = datetime.now(timezone.utc)

    return (
        db.query(UserOPDSToken)
        .filter(
            UserOPDSToken.user_id == user.id,
            UserOPDSToken.revoked_at == None,
            or_(UserOPDSToken.expires_at == None, UserOPDSToken.expires_at > now),
        )
        .order_by(UserOPDSToken.created_at.desc(), UserOPDSToken.id.desc())
    )


def create_opds_token(
        db: Session,
        user: User,
        name: str,
        expires_at: datetime | None = None,
) -> tuple[UserOPDSToken, str]:
    raw_token = generate_opds_token()
    token = UserOPDSToken(
        user_id=user.id,
        name=normalize_opds_token_name(name),
        token_hash=get_password_hash(raw_token),
        token_hint=get_opds_token_hint(raw_token),
        expires_at=expires_at,
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return token, raw_token


def list_active_opds_tokens(db: Session, user: User) -> list[UserOPDSToken]:
    return active_opds_token_query(db, user).all()


def authenticate_opds_token(
        db: Session,
        user: User,
        token_value: str,
        now: datetime | None = None,
) -> UserOPDSToken | None:
    if not token_value.startswith(OPDS_TOKEN_PREFIX):
        return None

    for token in active_opds_token_query(db, user, now=now).all():
        if verify_password(token_value, token.token_hash):
            return token

    return None


def mark_opds_token_used(db: Session, token: UserOPDSToken) -> None:
    now = datetime.now(timezone.utc)
    last_used_at = _as_utc(token.last_used_at)
    if last_used_at and now - last_used_at < OPDS_TOKEN_USED_UPDATE_INTERVAL:
        return

    token.last_used_at = now
    db.add(token)
    db.commit()


def revoke_opds_token(db: Session, user: User, token_id: int) -> UserOPDSToken | None:
    token = (
        db.query(UserOPDSToken)
        .filter(
            UserOPDSToken.id == token_id,
            UserOPDSToken.user_id == user.id,
            UserOPDSToken.revoked_at == None,
        )
        .first()
    )
    if not token:
        return None

    token.revoked_at = datetime.now(timezone.utc)
    db.add(token)
    db.commit()
    db.refresh(token)
    return token
