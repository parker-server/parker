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
