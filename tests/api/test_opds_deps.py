import pytest
from fastapi import HTTPException
from fastapi.security import HTTPBasicCredentials
from starlette.requests import Request

from app.api.opds_deps import OPDS_AUTH_CHALLENGE, get_current_user_opds
from app.models.setting import SystemSetting
from app.services.opds_tokens import create_opds_token, revoke_opds_token


def _make_request() -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/opds/",
        "raw_path": b"/opds/",
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope)


def _enable_opds(db) -> None:
    setting = db.query(SystemSetting).filter(SystemSetting.key == "server.opds_enabled").first()
    if not setting:
        setting = SystemSetting(
            key="server.opds_enabled",
            value="true",
            category="server",
            data_type="bool",
        )
        db.add(setting)
    else:
        setting.value = "true"
    db.commit()


def test_get_current_user_opds_accepts_account_password(db, normal_user):
    _enable_opds(db)

    user = get_current_user_opds(
        credentials=HTTPBasicCredentials(username=normal_user.username, password="test1234"),
        db=db,
        request=_make_request(),
    )

    assert user.id == normal_user.id


def test_get_current_user_opds_accepts_revocable_token(db, normal_user):
    _enable_opds(db)
    token, raw_token = create_opds_token(db, normal_user, "Tablet")

    user = get_current_user_opds(
        credentials=HTTPBasicCredentials(username=normal_user.username, password=raw_token),
        db=db,
        request=_make_request(),
    )
    db.refresh(token)

    assert user.id == normal_user.id
    assert token.last_used_at is not None


def test_get_current_user_opds_rejects_revoked_token(db, normal_user):
    _enable_opds(db)
    token, raw_token = create_opds_token(db, normal_user, "Tablet")
    revoke_opds_token(db, normal_user, token.id)

    with pytest.raises(HTTPException) as exc:
        get_current_user_opds(
            credentials=HTTPBasicCredentials(username=normal_user.username, password=raw_token),
            db=db,
            request=_make_request(),
        )

    assert exc.value.status_code == 401
    assert exc.value.headers["WWW-Authenticate"] == OPDS_AUTH_CHALLENGE
