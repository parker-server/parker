from datetime import timedelta
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.api.deps import SessionDep
from app.core.security import create_access_token, get_password_hash, verify_password
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
    token_type: str


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    is_superuser: bool


@router.post("/token", response_model=Token)
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

    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        subject=user.username, expires_delta=access_token_expires
    )

    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/register", response_model=UserResponse)
async def register_user(user_in: UserCreate, db: SessionDep):
    """
    Create a new user.
    First user created becomes Superuser automatically.
    """
    # Check if user exists
    user = db.query(User).filter(User.username == user_in.username).first()
    if user:
        raise HTTPException(
            status_code=400,
            detail="Username already registered",
        )

    # Check if this is the FIRST user
    count = db.query(User).count()
    is_first_user = (count == 0)

    db_user = User(
        email=user_in.email,
        username=user_in.username,
        hashed_password=get_password_hash(user_in.password),
        is_superuser=is_first_user  # Auto-promote first user
    )

    db.add(db_user)
    db.commit()
    db.refresh(db_user)

    return {
        "id": db_user.id,
        "username": db_user.username,
        "email": db_user.email,
        "is_superuser": db_user.is_superuser
    }


@router.get("/me", response_model=UserResponse)
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