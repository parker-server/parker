import os
from typing import ClassVar
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    app_name: ClassVar[str] = "Parker"
    version: ClassVar[str] = "0.1.8"
    
    database_url: str = "sqlite:///./storage/database/comics.db"
    #database_url: str = "sqlite:///./storage/database/temp.db"

    # --- BASE URL ---
    # Default to "/" for root, or "/comics" for subpath
    base_url: str = os.getenv("BASE_URL", "/")

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
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Helper to clean up the URL (ensure it starts with / and no trailing /)
    @property
    def clean_base_url(self):
        url = self.base_url.strip()
        if not url.startswith("/"):
            url = f"/{url}"
        return url.rstrip("/")


settings = Settings()