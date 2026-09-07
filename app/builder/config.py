"""Chargement de la configuration d'un client et de ses agents.

⚠️ **TEMPORAIRE** — les configurations sont lues depuis des fichiers JSON locaux
(`samples/configs/`) parce que les tables `ares_agent_config` et
`workspace_icp_config` sont encore vides et que le backend ne peut rien fournir
pour l'instant.

Quand le backend alimentera ces tables, il suffira de remplacer le corps de
`charger_workspace()` par une lecture en base : **la signature et le reste du code
ne bougent pas**. Les valeurs par défaut ci-dessous restent utiles dans les deux
cas — un client qui n'a rien réglé doit avoir un comportement défini.
"""
from __future__ import annotations

import json
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Optional
from uuid import UUID

from app.builder.roles import PROFILS_PAR_DEFAUT, ROLES
from app.schemas.ares import Palier
from app.schemas.config import ConfigAgent, ConfigWorkspace, SeuilPalier

# Répertoire des configurations de test. Disparaîtra avec la lecture en base.
DOSSIER_CONFIGS = Path(__file__).resolve().parents[2] / "samples" / "configs"

# Paliers du CDCF §4.3.1 — le défaut quand un agent n'a rien personnalisé.
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


def identifiant_agent(workspace_id: UUID | str, role: str) -> UUID:
    """Identifiant **déterministe** d'un agent, dérivé du workspace et du rôle.

    Un identifiant tiré au hasard changerait à chaque redémarrage tant que rien
    n'est persisté : le frontend perdrait l'agent qu'il vient d'afficher. Le
    dériver garantit qu'un même agent garde la même identité d'un appel à l'autre,
    et permet aux fichiers d'exemple de rester lisibles sans coller d'UUID.
    """
    return uuid.uuid5(uuid.NAMESPACE_URL, f"virelyon:agent:{workspace_id}:{role}")


def agent_par_defaut(workspace_id: UUID | str, role: str) -> ConfigAgent:
    """Un agent que le client n'a pas encore réglé.

    Volontairement prudent : mode supervision, email seul, ton professionnel, et
    statut `configuration_incomplete`. Un agent non configuré ne doit jamais
    envoyer quoi que ce soit tout seul.
    """
    nom, mission = PROFILS_PAR_DEFAUT.get(role, (role.capitalize(), ""))
    return ConfigAgent(
        agent_id=identifiant_agent(workspace_id, role),
        role=role,
        nom=nom,
        objectif_principal=mission,
        statut="configuration_incomplete",
        paliers=PALIERS_PAR_DEFAUT,
    )


def config_par_defaut(workspace_id: UUID | str) -> ConfigWorkspace:
    """Comportement d'un client qui n'a encore rien configuré.

    Il reçoit **l'équipe complète** — un prospecteur et un relanceur — plutôt
    qu'un agent unique : c'est l'organisation cible d'ARES, et un compte neuf doit
    la montrer telle qu'elle est, quitte à ce que les deux soient encore inactifs.
    """
    return ConfigWorkspace(
        workspace_id=UUID(str(workspace_id)),
        agents=[agent_par_defaut(workspace_id, role) for role in ROLES],
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


def _completer_agent(brut: dict, workspace_id: str) -> dict:
    """Complète un agent décrit dans un fichier avec ce qui peut être déduit.

    `agent_id` et `nom` sont dérivables du rôle : les exiger dans chaque fichier
    n'ajouterait que du bruit et des UUID collés à la main.
    """
    role = brut.get("role", "")
    nom, mission = PROFILS_PAR_DEFAUT.get(role, (role.capitalize(), ""))
    brut.setdefault("agent_id", str(identifiant_agent(workspace_id, role)))
    brut.setdefault("nom", nom)
    brut.setdefault("objectif_principal", mission)
    brut.setdefault("paliers", [p.model_dump() for p in PALIERS_PAR_DEFAUT])
    return brut


def charger_workspace(workspace_id: UUID | str) -> ConfigWorkspace:
    """Configuration du client, ou le défaut prudent s'il n'en a pas.

    Ne lève jamais : un client sans configuration est un cas normal, pas une
    erreur — c'est exactement l'état d'un compte qui vient d'être créé.
    """
    chemin = _fichiers().get(str(workspace_id))
    if chemin is None:
        return config_par_defaut(workspace_id)

    donnees = json.loads(chemin.read_text(encoding="utf-8"))
    donnees["agents"] = [
        _completer_agent(a, str(workspace_id)) for a in donnees.get("agents", [])
    ]
    return ConfigWorkspace(**donnees)


def charger_agent(
    workspace_id: UUID | str, agent_id: UUID | str
) -> Optional[ConfigAgent]:
    """L'agent désigné, **s'il appartient bien à ce workspace**. Sinon None."""
    return charger_workspace(workspace_id).agent(agent_id)


def vider_cache() -> None:
    """À appeler après avoir ajouté un fichier de configuration (tests)."""
    _fichiers.cache_clear()
