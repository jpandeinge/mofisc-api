# migrations/env.py

import logging
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Configure minimal logging for production
logging.basicConfig(
    level=logging.WARNING,  # Only log warnings and errors
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger("alembic")
# Disable SQL statement logging
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

# Add project root to Python path
project_root = Path(__file__).parents[2]
sys.path.append(str(project_root))

# Import models and settings
from api.app.config.settings import settings
from api.app.database import Base, get_connection_args
from api.app.models.transaction import Transaction
from api.app.models.user import User, UserProfile

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url():
    """Get database URL with proper connection parameters."""
    url = settings.DATABASE_URL
    if settings.SUPABASE_URL:
        if "?" in url:
            url += "&"
        else:
            url += "?"
        url += "application_name=galahad_migrations&sslmode=require&gssencmode=disable"
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_url()
    # Remove detailed logging of SQL operations
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        logging_level=logging.WARNING,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using connection pooling."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()

    # Configure connection pool
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.QueuePool,
        connect_args=get_connection_args(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        pool_recycle=1800,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_schemas=True,
            transaction_per_migration=True,
            render_as_batch=True,
            logging_level=logging.WARNING,  # Reduce logging level
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
