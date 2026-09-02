"""Base déclarative SQLAlchemy partagée par tous les modèles ORM du backend
unifié (CDCF Unifié v3.0).

Un seul `Base`/`metadata` pour l'ensemble des agents : Alembic autogenerate
a besoin d'une métadonnée unique pour comparer le schéma réel au schéma
attendu. Chaque module de modèles (ex. `models_apex.py`, plus tard
`models_ares.py`, etc.) importe cette classe et y déclare ses tables — il ne
doit jamais en créer une nouvelle.
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Classe de base déclarative unique pour tout le schéma applicatif."""
