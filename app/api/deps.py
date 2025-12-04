import logging
from typing import Generator, Annotated
from fastapi import Depends, HTTPException, status, Path, Request
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.orm import Session, joinedload
from pydantic import ValidationError, BaseModel
from fastapi import Query
from typing import TypeVar, Generic, Sequence

from app.database import SessionLocal
from app.config import settings
from app.models.user import User
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.library import Library

logger = logging.getLogger(__name__)

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
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)

async def get_token_hybrid(
    request: Request,
    token_auth: str = Depends(oauth2_scheme)
) -> str:
    """
    Extract token from Header (API) OR Cookie (Browser).
    """
    # 1. Try Header (FastAPI extracts this automatically via oauth2_scheme)
    if token_auth:
        return token_auth
    logger.info("No token found");
    # 2. Try Cookie (Fallback for HTML pages)
    cookie_token = request.cookies.get("access_token")
    if cookie_token:
        return cookie_token
    logger.info("No cookie found")
    # 3. Fail
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )

async def get_current_user(
        db: Annotated[Session, Depends(get_db)],
        token: Annotated[str, Depends(get_token_hybrid)]
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

async def get_current_active_superuser(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """
    Dependency that ensures the user is a Superuser (Admin).
    """
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=400, detail="The user doesn't have enough privileges"
        )
    return current_user

SessionDep = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(get_current_active_superuser)]

# --- LIBRARY DEPENDENCY ---
async def get_secure_library(
        library_id: Annotated[int, Path(title="The ID of the library")],
        db: SessionDep,
        user: CurrentUser
) -> Library:

    allowed_ids = [lib.id for lib in user.accessible_libraries]

    if not user.is_superuser:
        if library_id not in allowed_ids:
            raise HTTPException(status_code=404, detail="Library not found")

    """Get a specific library"""
    library = db.query(Library).filter(Library.id == library_id).first()
    if not library:
        raise HTTPException(status_code=404, detail="Library not found")

    return library


# --- SERIES DEPENDENCY ---
async def get_secure_series(
        series_id: Annotated[int, Path(title="The ID of the series")],
        db: SessionDep,
        user: CurrentUser
) -> Series:
    """
    Fetches a Series and enforces Library access.
    """
    query = db.query(Series).filter(Series.id == series_id)

    if not user.is_superuser:
        allowed_ids = [lib.id for lib in user.accessible_libraries]
        query = query.filter(Series.library_id.in_(allowed_ids))

    # Eager load library name for UI convenience
    series = query.options(joinedload(Series.library)).first()

    if not series:
        raise HTTPException(status_code=404, detail="Series not found")

    return series


# --- VOLUME DEPENDENCY ---
async def get_secure_volume(
        volume_id: Annotated[int, Path(title="The ID of the volume")],
        db: SessionDep,
        user: CurrentUser
) -> Volume:
    """
    Fetches a Volume and enforces Library access (via parent Series).
    """
    query = db.query(Volume).join(Series).filter(Volume.id == volume_id)

    if not user.is_superuser:
        allowed_ids = [lib.id for lib in user.accessible_libraries]
        query = query.filter(Series.library_id.in_(allowed_ids))

    volume = query.first()

    if not volume:
        raise HTTPException(status_code=404, detail="Volume not found")

    return volume


async def get_secure_comic(
        comic_id: Annotated[int, Path(title="The ID of the comic to get")],
        db: SessionDep,
        user: CurrentUser
) -> Comic:
    """
    Fetches a comic AND verifies the user has access to its library.
    Raises 404 if not found or restricted.
    """
    query = db.query(Comic).join(Volume).join(Series).filter(Comic.id == comic_id)

    if not user.is_superuser:
        allowed_ids = [lib.id for lib in user.accessible_libraries]
        query = query.filter(Series.library_id.in_(allowed_ids))

    comic = query.first()

    if not comic:
        # Standardize the error for both "Not Found" and "Unauthorized"
        raise HTTPException(status_code=404, detail="Comic not found")

    return comic

LibraryDep = Annotated[Library, Depends(get_secure_library)]
SeriesDep = Annotated[Series, Depends(get_secure_series)]
VolumeDep = Annotated[Volume, Depends(get_secure_volume)]
ComicDep = Annotated[Comic, Depends(get_secure_comic)]

