import asyncio

import pytest
from fastapi import HTTPException
from jose import jwt
from starlette.requests import Request

from app.api import deps
from app.config import settings
from app.core.security import get_password_hash
from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.series import Series
from app.models.user import User


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
    library = Library(name="deps-lib", path="/tmp/deps-lib")
    series = Series(name="Deps Series", library=library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="1",
        title="Deps Comic",
        filename="deps-comic.cbz",
        file_path="/tmp/deps-comic.cbz",
    )
    db.add_all([library, series, volume, comic])
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
    resolved = asyncio.run(deps.get_current_user(db=db, token=token))
    assert resolved.id == user.id

    bad_payload = jwt.encode({"foo": "bar"}, settings.secret_key, algorithm=settings.algorithm)
    with pytest.raises(HTTPException) as missing_sub:
        asyncio.run(deps.get_current_user(db=db, token=bad_payload))
    assert missing_sub.value.status_code == 401

    with pytest.raises(HTTPException) as invalid_token:
        asyncio.run(deps.get_current_user(db=db, token="not-a-jwt"))
    assert invalid_token.value.status_code == 401

    ghost_token = _make_token("missing-user")
    with pytest.raises(HTTPException) as missing_user:
        asyncio.run(deps.get_current_user(db=db, token=ghost_token))
    assert missing_user.value.status_code == 401


def test_get_current_user_optional_paths(db):
    user = _create_user(db, username="deps-optional", email="deps-optional@example.com")

    assert asyncio.run(deps.get_current_user_optional(db=db, token=None)) is None
    assert asyncio.run(deps.get_current_user_optional(db=db, token="not-a-jwt")) is None

    no_sub = jwt.encode({"foo": "bar"}, settings.secret_key, algorithm=settings.algorithm)
    assert asyncio.run(deps.get_current_user_optional(db=db, token=no_sub)) is None

    token = _make_token(user.username)
    resolved = asyncio.run(deps.get_current_user_optional(db=db, token=token))
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
        asyncio.run(deps.get_secure_library(library_id=library.id, db=db, user=user))
    assert missing_for_user.value.status_code == 404

    user.accessible_libraries.append(library)
    db.commit()

    allowed = asyncio.run(deps.get_secure_library(library_id=library.id, db=db, user=user))
    assert allowed.id == library.id

    admin_allowed = asyncio.run(deps.get_secure_library(library_id=library.id, db=db, user=admin))
    assert admin_allowed.id == library.id

    with pytest.raises(HTTPException) as missing_library:
        asyncio.run(deps.get_secure_library(library_id=999999, db=db, user=admin))
    assert missing_library.value.status_code == 404


def test_get_secure_series_volume_and_comic_paths(db):
    library, series, volume, comic = _seed_graph(db)
    user = _create_user(db, username="deps-sec-user", email="deps-sec-user@example.com")

    with pytest.raises(HTTPException):
        asyncio.run(deps.get_secure_series(series_id=series.id, db=db, user=user))
    with pytest.raises(HTTPException):
        asyncio.run(deps.get_secure_volume(volume_id=volume.id, db=db, user=user))
    with pytest.raises(HTTPException):
        asyncio.run(deps.get_secure_comic(comic_id=comic.id, db=db, user=user))

    user.accessible_libraries.append(library)
    db.commit()

    resolved_series = asyncio.run(deps.get_secure_series(series_id=series.id, db=db, user=user))
    resolved_volume = asyncio.run(deps.get_secure_volume(volume_id=volume.id, db=db, user=user))
    resolved_comic = asyncio.run(deps.get_secure_comic(comic_id=comic.id, db=db, user=user))

    assert resolved_series.id == series.id
    assert resolved_volume.id == volume.id
    assert resolved_comic.id == comic.id

    with pytest.raises(HTTPException) as missing_series:
        asyncio.run(deps.get_secure_series(series_id=999999, db=db, user=user))
    assert missing_series.value.status_code == 404

    with pytest.raises(HTTPException) as missing_volume:
        asyncio.run(deps.get_secure_volume(volume_id=999999, db=db, user=user))
    assert missing_volume.value.status_code == 404

    with pytest.raises(HTTPException) as missing_comic:
        asyncio.run(deps.get_secure_comic(comic_id=999999, db=db, user=user))
    assert missing_comic.value.status_code == 404
