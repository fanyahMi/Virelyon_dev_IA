"""Chargement de la configuration d'un client.

⚠️ **TEMPORAIRE** — les configurations sont lues depuis des fichiers JSON locaux
(`samples/configs/`) parce que les tables `ares_agent_config` et
`workspace_icp_config` de Supabase sont encore vides et que le backend ne peut
rien fournir pour l'instant.

Quand le backend alimentera ces tables, il suffira de remplacer le corps de
`charger_config()` par une lecture Supabase : **la signature et le reste du code
ne bougent pas**. Les valeurs par défaut ci-dessous restent utiles dans les deux
cas — un client qui n'a rien réglé doit avoir un comportement défini.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from uuid import UUID

from app.schemas.ares import Palier
from app.schemas.config import ConfigAgent, SeuilPalier

# Répertoire des configurations de test. Disparaîtra avec la lecture Supabase.
DOSSIER_CONFIGS = Path(__file__).resolve().parents[2] / "samples" / "configs"

# Paliers du CDCF §4.3.1 — le défaut quand un client n'a rien personnalisé.
PALIERS_PAR_DEFAUT: list[SeuilPalier] = [
    SeuilPalier(seuil=95, palier=Palier(nom="quasi_parfait", relances_max=5,
                                        cadence=[0, 3, 7, 12, 18, 25])),
    SeuilPalier(seuil=90, palier=Palier(nom="tres_forte", relances_max=4,
                                        cadence=[0, 3, 8, 15, 22])),
    SeuilPalier(seuil=70, palier=Palier(nom="correcte", relances_max=3,
                                        cadence=[0, 4, 10, 18])),
    SeuilPalier(seuil=0, palier=Palier(nom="faible", relances_max=1,
                                       cadence=[0, 7])),
]


def config_par_defaut(workspace_id: UUID | str) -> ConfigAgent:
    """Comportement d'un client qui n'a encore rien configuré.

    Volontairement prudent : mode supervision, email seul, ton professionnel.
    Un agent non configuré ne doit jamais envoyer quoi que ce soit tout seul.
    """
    return ConfigAgent(
        workspace_id=UUID(str(workspace_id)),
        statut="configuration_incomplete",
        paliers=PALIERS_PAR_DEFAUT,
    )


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


def charger_config(workspace_id: UUID | str) -> ConfigAgent:
    """Configuration du client, ou le défaut prudent s'il n'en a pas.

    Ne lève jamais : un client sans configuration est un cas normal, pas une
    erreur — c'est exactement l'état d'un compte qui vient d'être créé.
    """
    chemin = _fichiers().get(str(workspace_id))
    if chemin is None:
        return config_par_defaut(workspace_id)

    donnees = json.loads(chemin.read_text(encoding="utf-8"))
    donnees.setdefault("paliers", [p.model_dump() for p in PALIERS_PAR_DEFAUT])
    return ConfigAgent(**donnees)


def vider_cache() -> None:
    """À appeler après avoir ajouté un fichier de configuration (tests)."""
    _fichiers.cache_clear()
