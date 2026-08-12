import logging

from app.core.security import create_access_token, create_refresh_token


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

    db.refresh(normal_user)
    assert normal_user.last_login is not None


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
    }
