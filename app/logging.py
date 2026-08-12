import logging
from concurrent_log_handler import ConcurrentRotatingFileHandler
from pathlib import Path

from app.config import settings

class LogConfig:
    """Manages logging configuration with dynamic log level"""

    def __init__(self, log_dir: str = settings.log_dir, log_file: str = "parker.log"):
        self.log_dir = Path(log_dir)
        self.log_file = log_file
        self.logger = None

    def setup_logging(self, log_level: str = "INFO"):

        """Initialize logging with daily rotation"""
        # Create log directory if it doesn't exist
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Create logger
        self.logger = logging.getLogger("app")
        self.logger.setLevel(getattr(logging, log_level.upper()))

        # Remove existing handlers to avoid duplicates
        self.logger.handlers.clear()

        # File handler with daily rotation

        # Use ConcurrentRotatingFileHandler
        # - Rotates when file hits 10MB
        # - Keeps 10 backups
        # - Uses file locking so 4 workers don't crash each other
        file_handler = ConcurrentRotatingFileHandler(
            filename=self.log_dir / self.log_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=10,
            encoding="utf-8",
            use_gzip=True  # Optional: compress backups
        )

        # Console handler for development
        console_handler = logging.StreamHandler()

        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        # Add handlers
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

        return self.logger

    def update_log_level(self, log_level: str):
        """Update log level dynamically"""
        if self.logger:
            self.logger.setLevel(getattr(logging, log_level.upper()))
            self.logger.info(f"Log level updated to {log_level}")


# Global log config instance
log_config = LogConfig()