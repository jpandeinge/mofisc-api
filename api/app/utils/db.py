# api/app/utils/db.py

from contextlib import contextmanager
from time import sleep

import psycopg2
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from ..config.settings import settings
from .logging_config import get_logger
from .upstream import UpstreamUnavailable

logger = get_logger(__name__)


def is_database_disconnect(error: Exception) -> bool:
    """Only connection failures qualify; never retry SQL/auth/integrity errors."""
    if isinstance(error, DBAPIError):
        if error.connection_invalidated:
            return True
        error = error.orig
    if not isinstance(error, psycopg2.Error):
        return False
    code = error.pgcode
    if code:
        return code.startswith("08") or code in {"57P01", "57P02", "57P03"}
    message = str(error).lower()
    return any(
        fragment in message
        for fragment in (
            "server closed the connection",
            "connection reset",
            "connection refused",
            "socket is not connected",
            "could not receive data from server",
            "ssl syscall error",
            "ssl connection has been closed",
            "connection timed out",
            "timeout expired",
            "network is unreachable",
            "connection already closed",
        )
    )


engine = create_engine(
    settings.DATABASE_URL,
    **settings.database_pool_config,
    pool_pre_ping=True,
    pool_use_lifo=True,
    connect_args={
        "connect_timeout": settings.DB_CONNECT_TIMEOUT,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
    },
)


@event.listens_for(engine, "do_connect")
def connect_with_retry(dialect, connection_record, cargs, cparams):
    # Connecting has no transaction to replay. A failed query/commit, however,
    # must NOT be retried here: its writes may already have reached the server.
    for attempt in range(2):
        try:
            return dialect.loaded_dbapi.connect(*cargs, **cparams)
        except psycopg2.Error as error:
            if attempt or not is_database_disconnect(error):
                raise
            logger.warning("Retrying interrupted database connection")
            sleep(0.2)


@event.listens_for(engine, "handle_error")
def classify_disconnect(context):
    if is_database_disconnect(context.original_exception):
        context.is_disconnect = True


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def discard_session(db):
    try:
        db.invalidate()
    except DBAPIError:
        # Invalidation marks the connection unusable before closing its socket.
        # A second transport failure during close must not mask the first one.
        logger.warning("Database session invalidation encountered a closed socket")


def close_session(db, *, failed: bool = False):
    """A dead socket during rollback must not replace the request's outcome."""
    try:
        db.close()
    except DBAPIError as error:
        if not is_database_disconnect(error):
            if not failed:
                raise
            logger.exception("Database cleanup failed after request failure")
            return
        discard_session(db)
        logger.warning("Discarded disconnected database session during cleanup")


def run_db_task(db, operation, *args, **kwargs):
    """Run once, discarding a lost transaction without replaying its writes."""
    try:
        return operation(db, *args, **kwargs)
    except DBAPIError as error:
        if not is_database_disconnect(error):
            raise
        discard_session(db)
        logger.warning("Database operation unavailable after connection loss")
        raise UpstreamUnavailable("Database") from error


@contextmanager
def get_db_with_user(user_id: str):
    """Get database session with user context set"""
    with contextmanager(get_db)() as db:
        # Transaction-local context cannot leak to the next pooled user.
        db.execute(
            text("SELECT set_config('app.current_user_id', :user_id, true)"),
            {"user_id": str(user_id)},
        )
        yield db


def get_db():
    db = SessionLocal()
    failed = False
    try:
        yield db
    except BaseException:
        failed = True
        raise
    finally:
        close_session(db, failed=failed)
