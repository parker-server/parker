import asyncio
import inspect

import pytest
from fastapi import HTTPException
from jose import jwt
from starlette.requests import Request

from app.api import deps
from app.config import settings
from app.core.security import get_password_hash
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.user import User
from tests.factories import create_library_with_root


def _make_request(cookie_header: str | None = None) -> Request:
    headers = []
    if cookie_header:
        headers.append((b"cookie", cookie_header.encode("utf-8")))

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope)


def _seed_graph(db):
    library = create_library_with_root(db, "deps-lib", "/tmp/deps-lib")
    root = library.active_root
    series = Series(name="Deps Series", library=library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="1",
        title="Deps Comic",
        filename="deps-comic.cbz",
        library_root_id=root.id,
        relative_path="deps-comic.cbz",
    )
    db.add_all([series, volume, comic])
    db.commit()
    db.refresh(library)
    db.refresh(series)
    db.refresh(volume)
    db.refresh(comic)
    return library, series, volume, comic


def _create_user(db, *, username: str, email: str, is_superuser: bool = False):
    user = User(
        username=username,
        email=email,
        hashed_password=get_password_hash("pass1234"),
        is_superuser=is_superuser,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_token(username: str) -> str:
    return jwt.encode({"sub": username}, settings.secret_key, algorithm=settings.algorithm)


def test_get_db_closes_session(monkeypatch):
    events = []

    class DummySession:
        def close(self):
            events.append("closed")

    monkeypatch.setattr(deps, "SessionLocal", lambda: DummySession())

    gen = deps.get_db()
    session = next(gen)
    assert isinstance(session, DummySession)

    with pytest.raises(StopIteration):
        next(gen)

    assert events == ["closed"]


def test_get_token_hybrid_header_cookie_and_missing():
    from_header = asyncio.run(deps.get_token_hybrid(_make_request(), token_auth="header-token"))
    assert from_header == "header-token"

    from_cookie = asyncio.run(deps.get_token_hybrid(_make_request("access_token=cookie-token"), token_auth=""))
    assert from_cookie == "cookie-token"

    with pytest.raises(HTTPException) as exc:
        asyncio.run(deps.get_token_hybrid(_make_request(), token_auth=""))

    assert exc.value.status_code == 401
    assert exc.value.detail == "Not authenticated"


def test_get_token_optional_paths():
    assert asyncio.run(deps.get_token_optional(_make_request(), token_auth="header-token")) == "header-token"
    assert asyncio.run(deps.get_token_optional(_make_request("access_token=cookie-token"), token_auth="")) == "cookie-token"
    assert asyncio.run(deps.get_token_optional(_make_request(), token_auth="")) is None


def test_get_current_user_valid_and_error_paths(db):
    user = _create_user(db, username="deps-user", email="deps-user@example.com")

    token = _make_token(user.username)
    resolved = deps.get_current_user(db=db, token=token)
    assert resolved.id == user.id

    bad_payload = jwt.encode({"foo": "bar"}, settings.secret_key, algorithm=settings.algorithm)
    with pytest.raises(HTTPException) as missing_sub:
        deps.get_current_user(db=db, token=bad_payload)
    assert missing_sub.value.status_code == 401

    with pytest.raises(HTTPException) as invalid_token:
        deps.get_current_user(db=db, token="not-a-jwt")
    assert invalid_token.value.status_code == 401

    ghost_token = _make_token("missing-user")
    with pytest.raises(HTTPException) as missing_user:
        deps.get_current_user(db=db, token=ghost_token)
    assert missing_user.value.status_code == 401


def test_get_current_user_optional_paths(db):
    user = _create_user(db, username="deps-optional", email="deps-optional@example.com")

    assert deps.get_current_user_optional(db=db, token=None) is None
    assert deps.get_current_user_optional(db=db, token="not-a-jwt") is None

    no_sub = jwt.encode({"foo": "bar"}, settings.secret_key, algorithm=settings.algorithm)
    assert deps.get_current_user_optional(db=db, token=no_sub) is None

    token = _make_token(user.username)
    resolved = deps.get_current_user_optional(db=db, token=token)
    assert resolved.id == user.id


def test_get_current_active_superuser_guard(db):
    admin = _create_user(db, username="deps-admin", email="deps-admin@example.com", is_superuser=True)
    user = _create_user(db, username="deps-normal", email="deps-normal@example.com", is_superuser=False)

    resolved = asyncio.run(deps.get_current_active_superuser(current_user=admin))
    assert resolved.id == admin.id

    with pytest.raises(HTTPException) as exc:
        asyncio.run(deps.get_current_active_superuser(current_user=user))

    assert exc.value.status_code == 400


def test_get_secure_library_paths(db):
    library, _, _, _ = _seed_graph(db)
    admin = _create_user(db, username="deps-lib-admin", email="deps-lib-admin@example.com", is_superuser=True)
    user = _create_user(db, username="deps-lib-user", email="deps-lib-user@example.com")

    with pytest.raises(HTTPException) as missing_for_user:
        deps.get_secure_library(library_id=library.id, db=db, user=user)
    assert missing_for_user.value.status_code == 404

    user.accessible_libraries.append(library)
    db.commit()

    allowed = deps.get_secure_library(library_id=library.id, db=db, user=user)
    assert allowed.id == library.id

    admin_allowed = deps.get_secure_library(library_id=library.id, db=db, user=admin)
    assert admin_allowed.id == library.id

    with pytest.raises(HTTPException) as missing_library:
        deps.get_secure_library(library_id=999999, db=db, user=admin)
    assert missing_library.value.status_code == 404


def test_get_secure_series_volume_and_comic_paths(db):
    library, series, volume, comic = _seed_graph(db)
    user = _create_user(db, username="deps-sec-user", email="deps-sec-user@example.com")

    with pytest.raises(HTTPException):
        deps.get_secure_series(series_id=series.id, db=db, user=user)
    with pytest.raises(HTTPException):
        deps.get_secure_volume(volume_id=volume.id, db=db, user=user)
    with pytest.raises(HTTPException):
        deps.get_secure_comic(comic_id=comic.id, db=db, user=user)

    user.accessible_libraries.append(library)
    db.commit()

    resolved_series = deps.get_secure_series(series_id=series.id, db=db, user=user)
    resolved_volume = deps.get_secure_volume(volume_id=volume.id, db=db, user=user)
    resolved_comic = deps.get_secure_comic(comic_id=comic.id, db=db, user=user)

    assert resolved_series.id == series.id
    assert resolved_volume.id == volume.id
    assert resolved_comic.id == comic.id

    with pytest.raises(HTTPException) as missing_series:
        deps.get_secure_series(series_id=999999, db=db, user=user)
    assert missing_series.value.status_code == 404

    with pytest.raises(HTTPException) as missing_volume:
        deps.get_secure_volume(volume_id=999999, db=db, user=user)
    assert missing_volume.value.status_code == 404

    with pytest.raises(HTTPException) as missing_comic:
        deps.get_secure_comic(comic_id=999999, db=db, user=user)
    assert missing_comic.value.status_code == 404


def test_release_db_connection_returns_connection_to_pool(tmp_path):
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / 'pool.db'}")
    session = sessionmaker(bind=engine)()
    try:
        session.execute(text("select 1")).all()
        assert engine.pool.checkedout() == 1

        deps.release_db_connection(session)

        assert engine.pool.checkedout() == 0
        assert session.execute(text("select 1")).scalar() == 1
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize(
    "dependency",
    [
        deps.get_current_user,
        deps.get_current_user_optional,
        deps.get_secure_library,
        deps.get_secure_series,
        deps.get_secure_volume,
        deps.get_secure_comic,
    ],
)
def test_db_dependencies_are_sync_so_they_run_in_the_threadpool(dependency):
    # An async dependency would run its blocking query on the event loop and
    # freeze the whole worker while it waits for a pool connection.
    assert not inspect.iscoroutinefunction(dependency)
