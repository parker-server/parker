from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker, declarative_base
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


def refresh_query_planner_statistics(db: Session, only_if_missing: bool = False) -> bool:
    """
    Run ANALYZE so SQLite's query planner knows how big each table and index is (sqlite_stat1).
    Without statistics it guesses, and can pick very slow plans on large libraries.
    A full ANALYZE takes about a quarter of a second on a 73k-issue library. Sampling with
    analysis_limit was faster but skewed some estimates enough to pick worse plans.
    Returns True when ANALYZE ran.
    """
    if only_if_missing:
        has_statistics = db.execute(
            text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_stat1'")
        ).first()
        if has_statistics:
            return False

    db.execute(text("ANALYZE"))
    db.commit()
    return True
