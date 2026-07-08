"""
SQLite database engine + session management.

SQLite is used (as specified) to persist historical metrics, Kubernetes
events, restart history and RCA verdicts for trend analysis and the
dashboard's historical graphs.

`check_same_thread=False` + a pooled session-per-request pattern lets the
async collectors (running in background threads/tasks) and the FastAPI
request handlers share the same file safely. For higher write concurrency,
WAL mode is enabled below.
"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
import os

from app.core.config import settings

os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)

engine = create_engine(
    f"sqlite:///{settings.DB_PATH}",
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _):
    """Enable WAL mode + reasonable sync settings for concurrent R/W."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables. Called once at application startup."""
    from app.models import (  # noqa: F401  (imported for side effect: table registration)
        node, pod, metrics, longhorn, events, rca as rca_models, alerts
    )
    Base.metadata.create_all(bind=engine)
