import pytest
from pydantic import ValidationError

from app.config import INSECURE_SECRET_KEY_VALUES, Settings


def test_settings_requires_secret_key(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("secret_key", raising=False)

    with pytest.raises(ValidationError, match="SECRET_KEY must be set"):
        Settings(_env_file=None)


@pytest.mark.parametrize("placeholder", sorted(INSECURE_SECRET_KEY_VALUES))
def test_settings_rejects_insecure_secret_key_placeholders(placeholder):
    with pytest.raises(ValidationError, match="insecure placeholder"):
        Settings(secret_key=placeholder, _env_file=None)


def test_settings_rejects_legacy_uppercase_compose_placeholder():
    with pytest.raises(ValidationError, match="insecure placeholder"):
        Settings(secret_key="CHANGE_THIS_TO_A_REAL_SECRET_KEY", _env_file=None)


def test_settings_accepts_explicit_secret_key():
    value = "parker-unit-test-secret-key-not-for-production-000000"

    settings = Settings(secret_key=value, _env_file=None)

    assert settings.secret_key == value


def test_settings_reads_secret_key_from_environment(monkeypatch):
    value = "parker-env-secret-key-not-for-production-000000"
    monkeypatch.setenv("SECRET_KEY", value)

    settings = Settings(_env_file=None)

    assert settings.secret_key == value


def test_settings_reads_initial_admin_bootstrap_values_from_environment(monkeypatch):
    value = "parker-env-secret-key-not-for-production-000000"
    monkeypatch.setenv("SECRET_KEY", value)
    monkeypatch.setenv("INITIAL_ADMIN_USERNAME", "owner")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "owner-bootstrap-password")

    settings = Settings(_env_file=None)

    assert settings.initial_admin_username == "owner"
    assert settings.initial_admin_password == "owner-bootstrap-password"


def test_settings_allows_blank_initial_admin_password_until_bootstrap_is_needed():
    settings = Settings(
        secret_key="parker-unit-test-secret-key-not-for-production-000000",
        initial_admin_password="",
        _env_file=None,
    )

    assert settings.initial_admin_password == ""


def test_settings_rejects_blank_initial_admin_username():
    with pytest.raises(ValidationError, match="INITIAL_ADMIN_USERNAME cannot be empty"):
        Settings(
            secret_key="parker-unit-test-secret-key-not-for-production-000000",
            initial_admin_username="   ",
            _env_file=None,
        )


def test_debug_print_settings_redacts_bootstrap_password(monkeypatch, capsys):
    from app import config

    configured = Settings(
        secret_key="private-secret-key-value",
        initial_admin_password="private-bootstrap-password",
        _env_file=None,
    )
    monkeypatch.setattr(config, "settings", configured)

    config.debug_print_settings()

    output = capsys.readouterr().out
    assert "private-secret-key-value" not in output
    assert "private-bootstrap-password" not in output
    assert output.count("<redacted>") == 2
