from typing import Generator, Annotated
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from pydantic import ValidationError, BaseModel
from fastapi import Query
from typing import TypeVar, Generic, Sequence

from app.database import SessionLocal
from app.config import settings
from app.models.user import User


# 1. DATABASE DEPENDENCY
def get_db() -> Generator:
    try:
        db = SessionLocal()
        yield db
    finally:
        db.close()


# 2. PAGINATION DEPENDENCY
T = TypeVar("T")


class PaginationParams:
    def __init__(
            self,
            page: int = Query(1, ge=1, description="Page number"),
            size: int = Query(50, ge=1, le=100, description="Items per page"),
    ):
        self.page = page
        self.size = size
        self.skip = (page - 1) * size


class PaginatedResponse(BaseModel, Generic[T]):
    total: int
    page: int
    size: int
    items: Sequence[T]


# 3. AUTH DEPENDENCY
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


async def get_current_user(
        db: Annotated[Session, Depends(get_db)],
        token: Annotated[str, Depends(oauth2_scheme)]
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except (JWTError, ValidationError):
        raise credentials_exception

    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception

    return user