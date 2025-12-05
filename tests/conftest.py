import pytest
from typing import Generator
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import MagicMock

from app.api.deps import get_db
from app.core.security import get_password_hash
from app.database import Base
from app.main import app
from app.models.user import User
from app.api.deps import get_current_user


# --- FIXTURE START ---
@pytest.fixture(scope="session", autouse=True)
def mock_background_services():
    """
    Global patch to prevent background threads (Watcher, Scheduler)
    from trying to start during tests.
    """
    # Import the exact global instances your main.py uses
    from app.services.scheduler import scheduler_service
    from app.services.watcher import library_watcher

    # Replace their start/stop methods with empty mocks
    scheduler_service.start = MagicMock()
    scheduler_service.stop = MagicMock()

    library_watcher.start = MagicMock()
    library_watcher.stop = MagicMock()


# --- FIXTURE END ---

# 1. SETUP TEST DATABASE
# We use SQLite in-memory with StaticPool so the data persists
# for the duration of a single test function but isolates threads.
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# 2. DB SESSION FIXTURE
@pytest.fixture(scope="function")
def db():
    """
    Creates a fresh database for every single test case.
    """
    # Create Tables
    Base.metadata.create_all(bind=engine)

    session = TestingSessionLocal()
    yield session

    # Cleanup
    session.close()
    Base.metadata.drop_all(bind=engine)


# 3. CLIENT FIXTURE (Unauthenticated)
@pytest.fixture(scope="function")
def client(db) -> Generator:
    """
    Returns a TestClient with the database dependency overridden.
    """

    def override_get_db():
        try:
            yield db
        finally:
            # FIX: Do NOT close the session here!
            # The 'db' fixture handles the teardown at the end of the test function.
            pass

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as c:
        yield c

    # Reset overrides after test
    app.dependency_overrides.clear()


# 4. USER FIXTURES
@pytest.fixture(scope="function")
def normal_user(db):
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password=get_password_hash("test1234"),
        is_superuser=False,
        is_active=True
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture(scope="function")
def admin_user(db):
    user = User(
        username="admin",
        email="admin@example.com",
        hashed_password="fakehash",
        is_superuser=True,
        is_active=True
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# 5. AUTHENTICATED CLIENT FIXTURE
@pytest.fixture(scope="function")
def auth_client(client, normal_user):
    """
    Returns a client that is already "logged in" as a normal user.
    We do this by overriding the get_current_user dependency directly.
    """
    app.dependency_overrides[get_current_user] = lambda: normal_user
    return client


@pytest.fixture(scope="function")
def admin_client(client, admin_user):
    """
    Returns a client logged in as Admin.
    """
    app.dependency_overrides[get_current_user] = lambda: admin_user
    return client