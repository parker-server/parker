from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings

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
