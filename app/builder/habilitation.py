"""Contrôle d'habilitation : cet agent a-t-il le droit de faire cet appel ?

Trois questions, dans cet ordre — et l'ordre compte, car chacune suppose la
précédente :

1. **Appartenance** — l'agent existe-t-il dans CE workspace ? La recherche se fait
   dans la configuration du client, jamais dans un registre global : agir avec
   l'agent d'un autre client est structurellement impossible, même en connaissant
   son identifiant.
2. **Activité** — un agent en `supervision` peut travailler, un agent `inactif` ou
   `configuration_incomplete` non. C'est l'interrupteur du client.
3. **Capacité** — le poste autorise-t-il cet acte ? Voir `builder/roles.py`.

**`agent_id` reste facultatif.** Sans lui, l'appel se comporte comme avant : le
backend passe tout dans le corps et décide seul. C'est le mode actuel, et il doit
continuer de fonctionner tant que le backend n'a pas migré. Nommer un agent est
un engagement supplémentaire, pas un prérequis.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status

from app.builder.config import charger_workspace
from app.builder.roles import capacites
from app.schemas.config import ConfigAgent


def resoudre(
    workspace_id: UUID, agent_id: Optional[UUID], capacite: str
) -> Optional[ConfigAgent]:
    """L'agent habilité pour cet acte, ou None si aucun agent n'est nommé.

    Lève une `HTTPException` explicite plutôt que de laisser l'appel se poursuivre :
    un agent qui déborde de son poste doit échouer bruyamment, pas silencieusement.
    """
    if agent_id is None:
        return None

    agent = charger_workspace(workspace_id).agent(agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Aucun agent « {agent_id} » dans ce workspace.",
        )

    if agent.statut != "actif":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"L'agent « {agent.nom} » est en statut « {agent.statut} » : "
                f"il ne peut pas agir tant que le client ne l'a pas activé."
            ),
        )

    if not agent.peut(capacite):
        autorisees = ", ".join(sorted(capacites(agent.role))) or "aucune"
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"L'agent « {agent.nom} » ({agent.role}) n'a pas la capacité "
                f"« {capacite} ». Capacités de ce rôle : {autorisees}."
            ),
        )

    return agent
