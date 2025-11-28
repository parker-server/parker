from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    app_name: str = "Comic Server"
    database_url: str = "sqlite:///./storage/database/comics.db"

    # --- SECURITY SETTINGS ---
    # In production, generating a long random string is best:
    # openssl rand -hex 32
    secret_key: str = "CHANGE_THIS_TO_A_SECURE_RANDOM_KEY"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    # Paths
    unrar_path: str = r"C:\Program Files\WinRAR\UnRAR.exe"
    #unrar_path: str = "unrar"

    # Storage paths
    cache_dir: Path = Path("./storage/cache")
    cover_dir: Path = Path("./storage/cover")
    backup_dir: Path = Path("./storage/backup")
    #thumbnail_size: tuple = (300, 450)
    thumbnail_size: tuple = (320, 455)

    # Supported formats
    supported_extensions: list = [".cbz", ".cbr"]

    class Config:
        env_file = ".env"


settings = Settings()