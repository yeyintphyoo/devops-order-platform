import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import URL

from backend.app.database import Base
from backend.app import models  # noqa: F401


# Locate the project root and load environment variables.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# Allow DATABASE_URL to override the individual connection settings.
database_url = os.getenv("DATABASE_URL")

if not database_url:
    database_password = os.getenv("POSTGRES_PASSWORD")

    if not database_password:
        raise RuntimeError(
            "Database password is missing. Set POSTGRES_PASSWORD "
            "in .env or provide DATABASE_URL."
        )

    database_url = URL.create(
        drivername="postgresql+psycopg",
        username=os.getenv("POSTGRES_USER", "orders_user"),
        password=database_password,
        host=os.getenv("ALEMBIC_DB_HOST", "127.0.0.1"),
        port=int(os.getenv("ALEMBIC_DB_PORT", "5433")),
        database=os.getenv(
            "ALEMBIC_DB_NAME",
            os.getenv("POSTGRES_DB", "orders_db"),
        ),
    ).render_as_string(hide_password=False)

# Escape percent signs for ConfigParser.
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

# Make SQLAlchemy models available to Alembic autogeneration.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
