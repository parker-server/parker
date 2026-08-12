from datetime import datetime, timezone, timedelta
from typing import Optional, Any, Union
from jose import jwt
from passlib.context import CryptContext
import hashlib
from urllib.parse import quote
from app.config import settings

# Setup password hashing context (bcrypt)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def create_access_token(subject: Union[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """
    Generate a JWT (JSON Web Token) for a user.
    """
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)

    to_encode = {"exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
    return encoded_jwt

def create_refresh_token(subject: Union[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """
    Generate a long-lived Refresh Token.
    """
    # Default to 7 days if not specified
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(days=7)

    to_encode = {"exp": expire, "sub": str(subject), "type": "refresh"}
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)

    return encoded_jwt

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Check if a plain password matches the hashed version.
    """
    pre_hash = hashlib.sha256(plain_password.encode('utf-8')).hexdigest()
    return pwd_context.verify(pre_hash, hashed_password)


def get_password_hash(password: str) -> str:
    """
    Hash a password for storage.
    """
    # 3. PRE-HASH WITH SHA-256 (Fixes 72 byte limit)
    pre_hash = hashlib.sha256(password.encode('utf-8')).hexdigest()

    # 4. HASH THE HEX STRING (Always 64 chars, safe for bcrypt)
    return pwd_context.hash(pre_hash)


def get_redirect_url(requested_url_path: str, requested_url_query: str = None) -> str | None:

    if not requested_url_path:
        return None

    return_url = requested_url_path
    if requested_url_query:
        return_url += f"?{requested_url_query}"

    return quote(return_url)
