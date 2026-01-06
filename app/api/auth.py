from datetime import datetime, timezone, timedelta
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from jose import jwt, JWTError

from app.api.deps import SessionDep
from app.core.security import create_access_token, get_password_hash, verify_password, create_refresh_token
from app.models.user import User
from app.config import settings

router = APIRouter()


# Schema for Registration
class UserCreate(BaseModel):
    username: str
    password: str
    email: str


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    lifetime_in_seconds: int


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    is_superuser: bool

class RefreshRequest(BaseModel):
    refresh_token: str

@router.post("/token", response_model=Token, name="login_for_access_token")
async def login_for_access_token(
        form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
        db: SessionDep
):
    """
    OAuth2 compatible token login, get an access token for future requests
    """
    user = db.query(User).filter(User.username == form_data.username).first()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Update Last Login
    user.last_login = datetime.now(timezone.utc)
    db.commit()

    # 1. Create Short-Lived Access Token (e.g. 30 mins)
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        subject=user.username, expires_delta=access_token_expires
    )

    # 2. Create Long-Lived Refresh Token (e.g. 7 days)
    refresh_token = create_refresh_token(subject=user.username)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "lifetime_in_seconds": (settings.access_token_expire_minutes * 60)
    }


@router.post("/refresh", response_model=Token)
async def refresh_access_token(req: RefreshRequest, db: SessionDep):
    """
    Use a valid Refresh Token to get a new Access Token.
    This implements 'Sliding Expiration'.
    """
    try:
        # Decode and validate
        payload = jwt.decode(req.refresh_token, settings.secret_key, algorithms=[settings.algorithm])
        username: str = payload.get("sub")
        token_type: str = payload.get("type")

        if username is None or token_type != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")

        # Check if user still exists/is active
        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")

        # Success! Issue NEW Access Token
        access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
        access_token = create_access_token(
            subject=user.username, expires_delta=access_token_expires
        )

        # Rotate Refresh Token (Security Best Practice)
        # We issue a new refresh token so the old one can't be reused forever if stolen.
        new_refresh_token = create_refresh_token(subject=user.username)

        return {
            "access_token": access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer",
            "lifetime_in_seconds": (settings.access_token_expire_minutes * 60)
        }

    except JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")

@router.get("/me", response_model=UserResponse, name="read_users_me")
async def read_users_me(current_user: SessionDep):
    """
    Get current user details
    """
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "is_superuser": current_user.is_superuser
    }