from app.models.user import User


def _upload_payload(user_strategy="temp-password"):
    return {
        "data": {"user_strategy": user_strategy},
        "files": {"kavita_db_file": ("kavita.db", b"fake-db-bytes", "application/octet-stream")},
    }


def test_migration_run_rejects_unsupported_strategy(admin_client):
    payload = _upload_payload(user_strategy="first-login")

    response = admin_client.post("/api/migration/run", data=payload["data"], files=payload["files"])

    assert response.status_code == 400
    assert "only 'temp-password'" in response.json()["detail"].lower()


def test_migration_run_returns_csv_when_credentials_created(admin_client, monkeypatch):
    class FakeMigrationService:
        def __init__(self, db, kavita_db_path):
            self.closed = False

        def migrate_users(self, strategy="temp-password"):
            return "username,temporary_password,email,role\nuser1,pwd,test@example.com,User\n"

        def migrate_progress(self):
            return {"inserted": 1, "updated": 0, "skipped": 0}

        def close(self):
            self.closed = True

    monkeypatch.setattr("app.api.migration.KavitaMigrationService", FakeMigrationService)

    payload = _upload_payload()
    response = admin_client.post("/api/migration/run", data=payload["data"], files=payload["files"])

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=parker_migrated_credentials_" in response.headers["content-disposition"]
    assert "username,temporary_password" in response.text


def test_migration_run_returns_json_when_no_csv(admin_client, monkeypatch):
    class FakeMigrationService:
        def __init__(self, db, kavita_db_path):
            pass

        def migrate_users(self, strategy="temp-password"):
            return None

        def migrate_progress(self):
            return {"inserted": 2, "updated": 3, "skipped": 4}

        def close(self):
            pass

    monkeypatch.setattr("app.api.migration.KavitaMigrationService", FakeMigrationService)

    payload = _upload_payload()
    response = admin_client.post("/api/migration/run", data=payload["data"], files=payload["files"])

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "Migration of users and reading progress complete."
    assert body["details"] == {"inserted": 2, "updated": 3, "skipped": 4}


def test_migration_run_rolls_back_when_progress_phase_fails(admin_client, db, monkeypatch):
    class FailingMigrationService:
        def __init__(self, db, kavita_db_path):
            self.db = db

        def migrate_users(self, strategy="temp-password"):
            self.db.add(
                User(
                    username="rollback-user",
                    email="rollback-user@example.com",
                    hashed_password="hash",
                    is_superuser=False,
                    is_active=True,
                )
            )
            self.db.flush()
            return None

        def migrate_progress(self):
            raise RuntimeError("simulated migration failure")

        def close(self):
            pass

    monkeypatch.setattr("app.api.migration.KavitaMigrationService", FailingMigrationService)

    payload = _upload_payload()
    response = admin_client.post("/api/migration/run", data=payload["data"], files=payload["files"])

    assert response.status_code == 500
    assert "critical error" in response.json()["detail"].lower()

    rolled_back = db.query(User).filter(User.username == "rollback-user").first()
    assert rolled_back is None
