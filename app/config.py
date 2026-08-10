import os
from typing import ClassVar
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


INSECURE_SECRET_KEY_VALUES = {
    "change-me",
    "changeme",
    "change_this_to_a_real_secret_key",
    "change_this_to_a_secure_random_key",
    "your-secret-key-here",
}


def _split_comma_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    app_name: ClassVar[str] = "Parker"
    version: ClassVar[str] = "0.1.29"
    
    database_url: str = "sqlite:///./storage/database/comics.db"
    #database_url: str = "sqlite:///./storage/database/temp.db"

    # --- BASE URL ---
    # Default to "/" for root, or "/comics" for subpath
    base_url: str = "/"

    # --- ALLOWED ORIGINS ---
    # Comma-separated list of domains (e.g., "http://localhost:3000,http://localhost:8000")
    # Defaulting to ["*"] for local development
    allowed_origins_raw: str = Field(default="*", alias="ALLOWED_ORIGINS")

    # --- PROXY SETTINGS ---
    # Comma-separated list of proxy IPs (e.g., "127.0.0.1,172.18.0.1")
    # Defaulting to ["127.0.0.1"] for local development
    trusted_proxies_raw: str = Field(default="127.0.0.1", alias="TRUSTED_PROXIES")

    @property
    def allowed_origins(self) -> list[str]:
        return _split_comma_list(self.allowed_origins_raw)

    @property
    def trusted_proxies(self) -> list[str]:
        return _split_comma_list(self.trusted_proxies_raw)


    # --- SECURITY SETTINGS ---
    # Must be set explicitly. Generate a strong key with:
    # openssl rand -hex 32
    secret_key: str = Field(default="", repr=False, validate_default=True)
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    initial_admin_username: str = "admin"
    initial_admin_password: str = Field(default="", repr=False)

    @field_validator("secret_key")
    @classmethod
    def validate_secret_key(cls, value: str) -> str:
        key = value.strip()
        if not key:
            raise ValueError(
                "SECRET_KEY must be set to a unique random value. "
                "Generate one with: openssl rand -hex 32"
            )

        if key.lower() in INSECURE_SECRET_KEY_VALUES:
            raise ValueError(
                "SECRET_KEY is set to an insecure placeholder value. "
                "Generate a unique key with: openssl rand -hex 32"
            )

        return key

    @field_validator("initial_admin_username")
    @classmethod
    def validate_initial_admin_username(cls, value: str) -> str:
        username = value.strip()
        if not username:
            raise ValueError("INITIAL_ADMIN_USERNAME cannot be empty")
        return username

    # Paths
    unrar_path: str = "unrar"

    # Storage paths
    comics_path: Path = Path("/comics")
    log_dir: Path = Path("storage/logs")
    cache_dir: Path = Path("storage/cache")
    cover_dir: Path = Path("storage/cover")
    backup_dir: Path = Path("storage/backup")
    avatar_dir: Path = Path("storage/avatars")
    cbl_dir: Path = Path("storage/cbl")
    thumbnail_size: tuple[float, float] = (320, 455)
    avatar_size: tuple[float, float] = (400, 400)  # standard avatar box

    # Supported formats
    supported_extensions: list = [".cbz", ".cbr"]

    # --- NEW CONFIG STYLE ---
    model_config = SettingsConfigDict(env_file=".env",
                                      extra="ignore",
                                      env_ignore_empty=True,
                                      case_sensitive=False,
                                      env_nested_delimiter=None
                                      )

    # Helper to clean up the URL (ensure it starts with / and no trailing /)
    @property
    def clean_base_url(self):
        url = self.base_url.strip()
        if not url.startswith("/"):
            url = f"/{url}"
        return url.rstrip("/")


settings = Settings()

def debug_print_settings():
    import json
    values = settings.model_dump(mode="json")
    for secret_name in ("secret_key", "initial_admin_password"):
        if secret_name in values:
            values[secret_name] = "<redacted>"
    print("\n=== Parker Configuration ===")
    print(json.dumps(values, indent=2))
    print("Allowed origins:", settings.allowed_origins)
    print("Trusted proxies:", settings.trusted_proxies)
    print("=== End Configuration ===\n")

if os.getenv("PARKER_DEBUG") == "1":
    debug_print_settings()

