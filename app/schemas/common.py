"""Schémas communs (socle des requêtes, métadonnées de réponse)."""
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class RequeteAgent(BaseModel):
    """Socle de toute requête adressée à un agent d'un client.

    `agent_id` est **facultatif** : sans lui, l'appel se comporte comme avant, le
    backend fournissant tout dans le corps. Avec lui, le service vérifie que
    l'agent existe, qu'il est actif et que son rôle autorise l'acte demandé — puis
    applique sa configuration (mission, voix, canaux, paliers) plutôt que ce que
    l'appelant a bien voulu joindre. Voir `builder/habilitation.py`.
    """
    workspace_id: UUID
    agent_id: Optional[UUID] = None


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class Meta(BaseModel):
    """Métadonnées jointes à chaque réponse issue d'un appel LLM."""
    model_used: str
    usage: Usage
    cost_estimate: float
    cached: bool = False  # True si la réponse vient du cache (coût 0)
