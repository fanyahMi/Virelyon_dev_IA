"""Environnement Alembic (async) — CDCF Unifié v3.0.

La chaîne de connexion n'est jamais lue depuis alembic.ini : elle vient de
la configuration applicative (`app.core.config.get_settings().database_url`),
pour n'avoir qu'une seule source de vérité pour DATABASE_URL, partagée avec
le reste du backend (voir app/db/session.py).
"""
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Importer tous les modules de modèles ici : c'est ce qui rend leurs tables
# visibles à `target_metadata` (autogenerate). Un nouvel agent qui ajoute des
# modèles doit ajouter son import à cette liste.
from app.db.base import Base
from app.db import models_apex  # noqa: F401  (import nécessaire aux side-effects)
from app.core.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL n'est pas configurée : impossible d'exécuter les "
            "migrations Alembic. Renseigner DATABASE_URL dans .env."
        )
    return settings.database_url


def run_migrations_offline() -> None:
    """Génère le SQL sans se connecter à la base (mode --sql)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(
        configuration, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
