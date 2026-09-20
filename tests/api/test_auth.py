import logging

from app.core.security import create_access_token, create_refresh_token


def _use_test_db_for_password_change_middleware(monkeypatch, db):
    class SessionProxy:
        def __init__(self, session):
            self.session = session

        def query(self, *args, **kwargs):
            return self.session.query(*args, **kwargs)

        def close(self):
            pass

    monkeypatch.setattr("app.main.SessionLocal", lambda: SessionProxy(db))


def test_login_for_access_token_success_updates_last_login(client, db, normal_user):
    response = client.post(
        "/api/auth/token",
        data={"username": normal_user.username, "password": "test1234"},
    )

    assert response.status_code == 200
    payload = response.json()

    assert payload["token_type"] == "bearer"
    assert payload["access_token"]
    assert payload["refresh_token"]
    assert payload["lifetime_in_seconds"] > 0
    assert payload["must_change_password"] is False

    db.refresh(normal_user)
    assert normal_user.last_login is not None


def test_login_for_access_token_reports_required_password_change(client, db, normal_user):
    normal_user.must_change_password = True
    db.commit()

    response = client.post(
        "/api/auth/token",
        data={"username": normal_user.username, "password": "test1234"},
    )

    assert response.status_code == 200
    assert response.json()["must_change_password"] is True


def test_login_for_access_token_rejects_invalid_credentials(client, normal_user):
    response = client.post(
        "/api/auth/token",
        data={"username": normal_user.username, "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect username or password"


def test_login_for_access_token_logs_invalid_password(client, normal_user, caplog):
    caplog.set_level(logging.WARNING, logger="app.auth")

    response = client.post(
        "/api/auth/token",
        data={"username": normal_user.username, "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert any(
        "Authentication failed via password login" in record.message
        and "reason=invalid_password" in record.message
        and f"username='{normal_user.username}'" in record.message
        for record in caplog.records
    )


def test_login_for_access_token_logs_unknown_user(client, caplog):
    caplog.set_level(logging.WARNING, logger="app.auth")

    response = client.post(
        "/api/auth/token",
        data={"username": "missing-user", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert any(
        "Authentication failed via password login" in record.message
        and "reason=unknown_user" in record.message
        and "username='missing-user'" in record.message
        for record in caplog.records
    )


def test_refresh_access_token_success(client, normal_user):
    refresh_token = create_refresh_token(subject=normal_user.username)

    response = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})

    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["access_token"]
    assert payload["refresh_token"]
    assert payload["must_change_password"] is False


def test_refresh_access_token_rejects_non_refresh_token(client, normal_user):
    access_token = create_access_token(subject=normal_user.username)

    response = client.post("/api/auth/refresh", json={"refresh_token": access_token})

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid refresh token"


def test_refresh_access_token_rejects_invalid_token(client):
    response = client.post("/api/auth/refresh", json={"refresh_token": "not-a-real-token"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


def test_read_users_me(auth_client, normal_user):
    response = auth_client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json() == {
        "id": normal_user.id,
        "username": normal_user.username,
        "email": normal_user.email,
        "is_superuser": False,
        "must_change_password": False,
    }


def test_required_password_change_redirects_html_navigation(client, db, normal_user, monkeypatch):
    _use_test_db_for_password_change_middleware(monkeypatch, db)
    normal_user.must_change_password = True
    db.commit()
    token = create_access_token(subject=normal_user.username)
    client.cookies.set("access_token", token)

    response = client.get(
        "/user/dashboard",
        headers={"accept": "text/html"},
        follow_redirects=False,
    )

    assert response.status_code == 307
    assert response.headers["location"] == "/user/change-password?next=/user/dashboard"


def test_required_password_change_blocks_regular_api_calls(client, db, normal_user, monkeypatch):
    _use_test_db_for_password_change_middleware(monkeypatch, db)
    normal_user.must_change_password = True
    db.commit()
    token = create_access_token(subject=normal_user.username)

    response = client.get(
        "/api/users/me/preferences",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Password change required"


def test_required_password_change_page_and_password_update_are_allowed(client, db, normal_user, monkeypatch):
    _use_test_db_for_password_change_middleware(monkeypatch, db)
    normal_user.must_change_password = True
    db.commit()
    token = create_access_token(subject=normal_user.username)
    client.cookies.set("access_token", token)

    page = client.get("/user/change-password")
    assert page.status_code == 200
    assert "Set your own password before continuing." in page.text

    update = client.put(
        "/api/users/me/password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "test1234", "new_password": "newpassword1"},
    )

    assert update.status_code == 200
    db.refresh(normal_user)
    assert normal_user.must_change_password is False

    preferences = client.get(
        "/api/users/me/preferences",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert preferences.status_code == 200
