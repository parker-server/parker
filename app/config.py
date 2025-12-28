import os
from typing import ClassVar
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


def _split_comma_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    app_name: ClassVar[str] = "Parker"
    version: ClassVar[str] = "0.1.16"
    
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
    # In production, generating a long random string is best:
    # openssl rand -hex 32
    secret_key: str = "CHANGE_THIS_TO_A_SECURE_RANDOM_KEY"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    # Paths
    unrar_path: str = "unrar"

    # Storage paths
    log_dir: Path = Path("storage/logs")
    cache_dir: Path = Path("storage/cache")
    cover_dir: Path = Path("storage/cover")
    backup_dir: Path = Path("storage/backup")
    avatar_dir: Path = Path("storage/avatars")
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
    print("\n=== Parker Configuration ===")
    print(json.dumps(settings.model_dump(mode="json"), indent=2))
    print("Allowed origins:", settings.allowed_origins)
    print("Trusted proxies:", settings.trusted_proxies)
    print("=== End Configuration ===\n")