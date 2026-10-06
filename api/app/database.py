# api/app/database.py

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from .config.settings import settings


def get_connection_args():
    """Get database connection arguments."""
    connect_args = {"connect_timeout": 60, "application_name": "galahad_app"}
    if settings.SUPABASE_URL:
        connect_args.update({"sslmode": "require", "gssencmode": "disable"})
    return connect_args


def create_database_url():
    """Create database URL with proper parameters."""
    url = settings.DATABASE_URL
    if settings.SUPABASE_URL:
        if "?" in url:
            url += "&"
        else:
            url += "?"
        url += "application_name=galahad_app&sslmode=require&gssencmode=disable"
    return url


engine = create_engine(
    create_database_url(),
    poolclass=QueuePool,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=True,
    connect_args=get_connection_args(),
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
