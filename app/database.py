import logging
import time
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings

logger = logging.getLogger(__name__)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False, "timeout": 60}  # SQLite specific
)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL") # Optional: Faster, slightly less safe on power loss

    # Increase cache to ~64MB (negative value = kilobytes)
    # Default is ~2MB, which is too small for large comic libraries.
    cursor.execute("PRAGMA cache_size=-64000")

    cursor.close()

SessionLocal = sessionmaker(autoflush=False, bind=engine)

Base = declarative_base()


def commit_with_retry(db, attempts: int = 5, delay: float = 1.0) -> None:
    """
    Commit with retries on transient SQLite 'database is locked' errors.
    The connection's busy_timeout covers most contention, but batch writers
    (scan/thumbnail workers) hold locks long enough that it can still be
    exceeded, so this mirrors ScanManager's retry pattern for those scenarios.
    """
    for attempt in range(attempts):
        try:
            db.commit()
            return
        except OperationalError as e:
            if "locked" in str(e).lower() and attempt < attempts - 1:
                logger.warning(f"DB Locked during commit (attempt {attempt + 1}/{attempts}). Retrying...")
                time.sleep(delay)
                continue
            raise
