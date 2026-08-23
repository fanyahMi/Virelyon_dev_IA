"""Configuration d'un client — ce que l'Agent Builder produit et qu'ARES consomme.

Un seul objet regroupe ce qui est aujourd'hui éclaté sur deux tables Supabase
(`ares_agent_config` et `workspace_icp_config`) plus deux réglages qui n'y
existent pas encore (`objectif_principal`, `ton_de_voix`).

**Règle** : aucune valeur de comportement en dur dans les modules ARES. Si une
constante métier traîne dans le code, c'est un bug de configuration — le même
code doit produire des comportements différents selon le client.
"""
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.ares import ICP, Palier, ScoringConfig


class SeuilPalier(BaseModel):
    """À partir de quel score ce palier s'applique."""
    seuil: int = Field(ge=0, le=100)
    palier: Palier


class ConfigAgent(BaseModel):
    """Tout ce qu'ARES doit savoir du client pour se comporter comme lui."""

    workspace_id: UUID

    # --- Ciblage (workspace_icp_config) -------------------------------------
    icp: ICP = Field(default_factory=ICP)
    # Zone de prospection — jamais déduite, uniquement saisie (CDCF §8).
    zone: Optional[str] = None

    # --- Mission et voix (absents de la base — voir §8 du guide Fullstack) ---
    objectif_principal: str = ""
    ton_de_voix: str = "professionnel"
    langue: str = "fr"

    # --- Exécution (ares_agent_config) --------------------------------------
    statut: Literal["configuration_incomplete", "inactif", "actif"] = "inactif"
    # `supervision` : chaque message passe en validation humaine avant envoi.
    autonomy_level: Literal["supervision", "autonome"] = "supervision"
    canaux_actifs: list[str] = Field(default_factory=lambda: ["email"])
    quotas_par_canal: dict = Field(default_factory=dict)

    # --- Décision -----------------------------------------------------------
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
