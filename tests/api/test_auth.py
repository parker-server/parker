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


def test_required_password_change_lookup_runs_off_the_event_loop(client, normal_user, monkeypatch):
    import asyncio

    calls = []

    def fake_lookup(username):
        try:
            asyncio.get_running_loop()
            calls.append((username, "event loop"))
        except RuntimeError:
            calls.append((username, "threadpool"))
        return False

    monkeypatch.setattr("app.main._user_must_change_password", fake_lookup)
    token = create_access_token(subject=normal_user.username)

    client.get("/api/users/me/preferences", headers={"Authorization": f"Bearer {token}"})

    assert calls == [(normal_user.username, "threadpool")]


def _count_password_change_lookups(monkeypatch, must_change: bool) -> list[str]:
    calls = []

    def fake_lookup(username):
        calls.append(username)
        return must_change

    monkeypatch.setattr("app.main._user_must_change_password", fake_lookup)
    return calls


def test_required_password_change_check_remembers_users_who_dont_need_it(client, normal_user, monkeypatch):
    calls = _count_password_change_lookups(monkeypatch, must_change=False)
    headers = {"Authorization": f"Bearer {create_access_token(subject=normal_user.username)}"}

    for _ in range(3):
        assert client.get("/api/users/me/preferences", headers=headers).status_code != 403

    assert calls == [normal_user.username]


def test_required_password_change_check_always_rechecks_users_who_must_change(client, normal_user, monkeypatch):
    # Caching "must change" would keep redirecting a user after they changed it
    calls = _count_password_change_lookups(monkeypatch, must_change=True)
    headers = {"Authorization": f"Bearer {create_access_token(subject=normal_user.username)}"}

    for _ in range(2):
        assert client.get("/api/users/me/preferences", headers=headers).status_code == 403

    assert calls == [normal_user.username, normal_user.username]


def test_required_password_change_check_rechecks_after_cache_expires(client, normal_user, monkeypatch):
    monkeypatch.setattr("app.core.security.PASSWORD_CHANGE_CACHE_SECONDS", 0)
    calls = _count_password_change_lookups(monkeypatch, must_change=False)
    headers = {"Authorization": f"Bearer {create_access_token(subject=normal_user.username)}"}

    client.get("/api/users/me/preferences", headers=headers)
    client.get("/api/users/me/preferences", headers=headers)

    assert calls == [normal_user.username, normal_user.username]


def test_admin_forcing_password_change_applies_at_once_in_the_same_worker(
        admin_client, db, normal_user, monkeypatch
):
    _use_test_db_for_password_change_middleware(monkeypatch, db)
    user_headers = {"Authorization": f"Bearer {create_access_token(subject=normal_user.username)}"}
    assert admin_client.get("/api/users/me/preferences", headers=user_headers).status_code == 200

    response = admin_client.patch(
        f"/api/users/{normal_user.id}",
        json={"email": normal_user.email, "must_change_password": True},
    )
    assert response.status_code == 200

    blocked = admin_client.get("/api/users/me/preferences", headers=user_headers)
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "Password change required"


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
