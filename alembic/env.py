"""Alembic environment (CloudGuardian AI, gap #4).

Reads DATABASE_URL from the environment (default = the compose Postgres),
so migrations run against the same database the services use without
baking credentials into alembic.ini.
"""
import os
from logging.config import fileConfig

from alembic import context

config = context.config

config.set_main_option(
    "sqlalchemy.url",
    os.getenv(
        "DATABASE_URL",
        "postgresql://cloudguardian:cloudguardian@postgres:5432/cloudguardian",
    ),
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import create_engine

    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    with engine.connect() as connection:
        context.configure(connection=connection, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()