from types import SimpleNamespace

import pytest

from app.core.security import verify_password, get_password_hash
from app.models.user import User
from app.services.admin_bootstrap import AdminBootstrapError, ensure_initial_admin


def _settings(username: str = "admin", password: str = "unique-admin-password-123"):
    return SimpleNamespace(
        initial_admin_username=username,
        initial_admin_password=password,
    )


def test_ensure_initial_admin_creates_default_admin_when_no_superuser_exists(db):
    user = ensure_initial_admin(db, app_settings=_settings())

    assert user is not None
    assert user.username == "admin"
    assert user.email == "admin@example.com"
    assert user.is_superuser is True
    assert user.is_active is True
    assert verify_password("unique-admin-password-123", user.hashed_password)


def test_ensure_initial_admin_uses_configured_username(db):
    user = ensure_initial_admin(db, app_settings=_settings(username="owner"))

    assert user is not None
    assert user.username == "owner"
    assert user.email == "owner@parker.local"
    assert user.is_superuser is True


def test_ensure_initial_admin_requires_password_when_bootstrap_is_needed(db):
    with pytest.raises(AdminBootstrapError, match="INITIAL_ADMIN_PASSWORD is not set"):
        ensure_initial_admin(db, app_settings=_settings(password=""))

    assert db.query(User).count() == 0


def test_ensure_initial_admin_rejects_insecure_password_placeholder(db):
    with pytest.raises(AdminBootstrapError, match="insecure placeholder"):
        ensure_initial_admin(db, app_settings=_settings(password="password123"))

    assert db.query(User).count() == 0


def test_ensure_initial_admin_rejects_short_password(db):
    with pytest.raises(AdminBootstrapError, match="at least 8 characters"):
        ensure_initial_admin(db, app_settings=_settings(password="short"))

    assert db.query(User).count() == 0


def test_ensure_initial_admin_does_not_modify_existing_superuser(db):
    existing_hash = get_password_hash("admin")
    user = User(
        username="admin",
        email="admin@example.com",
        hashed_password=existing_hash,
        is_superuser=True,
        is_active=True,
    )
    db.add(user)
    db.commit()

    created = ensure_initial_admin(db, app_settings=_settings(password=""))

    assert created is None
    db.refresh(user)
    assert user.hashed_password == existing_hash
    assert verify_password("admin", user.hashed_password)


def test_ensure_initial_admin_rejects_existing_username_when_no_superuser_exists(db):
    db.add(
        User(
            username="admin",
            email="admin@example.com",
            hashed_password=get_password_hash("reader-password"),
            is_superuser=False,
            is_active=True,
        )
    )
    db.commit()

    with pytest.raises(AdminBootstrapError, match="username already exists"):
        ensure_initial_admin(db, app_settings=_settings())

    assert db.query(User).filter(User.is_superuser == True).count() == 0
