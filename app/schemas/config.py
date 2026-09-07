"""Configuration d'un client — ce que l'Agent Builder produit et qu'ARES consomme.

Deux niveaux, et la frontière entre les deux est un garde-fou du CDCF, pas un
choix esthétique :

- **`ConfigWorkspace`** — ce que partagent tous les agents d'un client : l'ICP, la
  zone, la langue. L'ICP a une **source unique** par workspace (CDCF §0, garde-fou
  n°2) : le dupliquer par agent, c'est accepter que deux agents du même client
  finissent par cibler deux marchés différents sans que personne s'en aperçoive.
- **`ConfigAgent`** — ce qui appartient à un agent : sa mission, sa voix, ses
  canaux, ses paliers, son autonomie. C'est ce qui fait qu'un prospecteur et un
  relanceur du même client ne se comportent pas pareil.

**Règle** : aucune valeur de comportement en dur dans les modules ARES. Si une
constante métier traîne dans le code, c'est un bug de configuration — le même
code doit produire des comportements différents selon le client *et* selon l'agent.
"""
from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.builder.roles import peut as role_peut
from app.schemas.ares import ICP, Palier, ScoringConfig


class SeuilPalier(BaseModel):
    """À partir de quel score ce palier s'applique."""
    seuil: int = Field(ge=0, le=100)
    palier: Palier


class ConfigAgent(BaseModel):
    """Un agent nommé, au service d'un client. Ce qu'il est et comment il agit."""

    agent_id: UUID
    role: Literal["prospecteur", "relanceur"]
    # Nom affiché dans le tableau de bord (« Prospecteur », « Relanceur »).
    nom: str = ""

    # --- Mission et voix -----------------------------------------------------
    # `objectif_principal` part dans le prompt de génération : c'est ce qui
    # distingue les messages de deux clients, et de deux agents du même client.
    objectif_principal: str = ""
    ton_de_voix: str = "professionnel"

    # --- Exécution -----------------------------------------------------------
    statut: Literal["configuration_incomplete", "inactif", "actif"] = "inactif"
    # `supervision` : chaque message passe en validation humaine avant envoi.
    autonomy_level: Literal["supervision", "autonome"] = "supervision"
    canaux_actifs: list[str] = Field(default_factory=lambda: ["email"])
    quotas_par_canal: dict = Field(default_factory=dict)

    # --- Décision ------------------------------------------------------------
    scoring_config: ScoringConfig = Field(default_factory=ScoringConfig)
    # Paliers de relance, du seuil le plus haut au plus bas (CDCF §4.3.1).
    paliers: list[SeuilPalier] = Field(default_factory=list)
    # En dessous, la qualification part en file manuelle (`a_valider`) plutôt
    # que d'être tranchée : garde-fou « jamais de rejet silencieux » (CDCF §0).
    seuil_confiance: float = Field(default=0.7, ge=0, le=1)

    def palier_pour(self, score: int) -> Palier:
        """Palier applicable à ce score. Le dernier fait office de repli."""
        for entree in self.paliers:
            if score >= entree.seuil:
                return entree.palier
        return self.paliers[-1].palier

    def peut(self, capacite: str) -> bool:
        """Ce poste autorise-t-il cet acte ? Voir `builder/roles.py`."""
        return role_peut(self.role, capacite)


class ConfigWorkspace(BaseModel):
    """Le client, et les agents qui travaillent pour lui."""

    workspace_id: UUID

    # --- Ciblage — partagé par tous les agents (CDCF §0, garde-fou n°2) ------
    icp: ICP = Field(default_factory=ICP)
    # Zone de prospection — jamais déduite, uniquement saisie (CDCF §8).
    zone: Optional[str] = None
    langue: str = "fr"

    agents: list[ConfigAgent] = Field(default_factory=list)

    def agent(self, agent_id: UUID | str) -> Optional[ConfigAgent]:
        """L'agent désigné, ou None s'il n'appartient pas à ce workspace.

        C'est aussi le contrôle d'appartenance : chercher l'agent **dans** le
        workspace rend structurellement impossible d'agir avec l'agent d'un
        autre client, même en connaissant son identifiant.
        """
        return next((a for a in self.agents if str(a.agent_id) == str(agent_id)), None)

    def par_role(self, role: str) -> Optional[ConfigAgent]:
        """Premier agent tenant ce poste — raccourci pour un workspace standard."""
        return next((a for a in self.agents if a.role == role), None)
