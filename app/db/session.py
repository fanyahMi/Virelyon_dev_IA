"""Accès à la base de données (Postgres/Supabase) — SQLAlchemy async + asyncpg.

CDCF Unifié v3.0 : le backend unique accède directement à Postgres (plus de
service IA sans état côté données). Ce module ne fait qu'ouvrir/fournir des
sessions ; il ne contient aucune logique métier.

L'engine est créé paresseusement (au premier appel), pas à l'import du
module : importer ce fichier ne doit jamais échouer, même si `DATABASE_URL`
n'est pas encore configurée (ex. tests unitaires qui ne touchent pas la DB).
"""
from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


class DatabaseNotConfiguredError(RuntimeError):
    """`DATABASE_URL` est absente ou vide alors qu'un accès DB est requis."""


@lru_cache
def get_engine() -> AsyncEngine:
    """Engine async partagé, créé une seule fois par process.

    Mise en cache via `lru_cache` (même schéma que `get_settings`) : un seul
    pool de connexions pour toute la durée de vie du process.
    """
    settings = get_settings()
    if not settings.database_url:
        raise DatabaseNotConfiguredError(
            "DATABASE_URL n'est pas configurée : impossible d'accéder à la "
            "base de données. Renseigner DATABASE_URL dans .env "
            "(format : postgresql+asyncpg://user:password@host:port/dbname)."
        )
    return create_async_engine(settings.database_url, pool_pre_ping=True)


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Fabrique de sessions async, liée à l'engine partagé."""
    return async_sessionmaker(bind=get_engine(), expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dépendance FastAPI : fournit une session DB par requête HTTP.

    Usage : `db: AsyncSession = Depends(get_db)` dans un routeur.
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session
