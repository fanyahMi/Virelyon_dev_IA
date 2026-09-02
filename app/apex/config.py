"""Chargement de la configuration APEX d'un client.

⚠️ **TEMPORAIRE** — même raison que app/builder/config.py : la table
`agent_config` (apex) de Supabase est encore vide côté backend, donc les
configurations sont lues depuis des fichiers JSON locaux
(`samples/configs_apex/`). Quand le backend alimentera la table, seul le corps
de `charger_config()` change — signature et reste du code inchangés.

Dossier séparé de `samples/configs/` (ARES) : les deux loaders indexent par
`workspace_id` sans distinguer l'agent, un même dossier ferait courir un
risque de collision entre une config ARES et une config APEX partageant (par
erreur) le même identifiant.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from uuid import UUID

from app.schemas.apex import ConfigAgentApex

DOSSIER_CONFIGS = Path(__file__).resolve().parents[2] / "samples" / "configs_apex"


def config_par_defaut(workspace_id: UUID | str) -> ConfigAgentApex:
    """Comportement d'un client qui n'a encore rien configuré : Niveau 1
    Supervisé, un seul canal, seuils prudents (CDCF §4.14, « États & limites »
    : « Tout nouveau workspace démarre en Niveau 1 par défaut »)."""
    return ConfigAgentApex(workspace_id=UUID(str(workspace_id)))


@lru_cache(maxsize=64)
def _fichiers() -> dict[str, Path]:
    """Index workspace_id → fichier, construit une fois."""
    if not DOSSIER_CONFIGS.is_dir():
        return {}
    index: dict[str, Path] = {}
    for chemin in DOSSIER_CONFIGS.glob("*.json"):
        try:
            donnees = json.loads(chemin.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        identifiant = donnees.get("workspace_id")
        if identifiant:
            index[str(identifiant)] = chemin
    return index


def charger_config(workspace_id: UUID | str) -> ConfigAgentApex:
    """Configuration du client, ou le défaut prudent s'il n'en a pas.

    Ne lève jamais : un client sans configuration est un cas normal (compte
    fraîchement créé), pas une erreur — identique à builder/config.py.
    """
    chemin = _fichiers().get(str(workspace_id))
    if chemin is None:
        return config_par_defaut(workspace_id)

    donnees = json.loads(chemin.read_text(encoding="utf-8"))
    return ConfigAgentApex(**donnees)


def vider_cache() -> None:
    """À appeler après avoir ajouté un fichier de configuration (tests)."""
    _fichiers.cache_clear()
