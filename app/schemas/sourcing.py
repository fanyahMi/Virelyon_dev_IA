"""Contrats du sourcing — exécution d'un plan de recherche sur les sources externes."""
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.ares import ICP, Lead
from app.schemas.builder import Diagnostic


class RequeteHTTP(BaseModel):
    """L'appel qui serait envoyé à la source. Sert au mode `dry_run` et au débogage.

    ⚠️ La clé d'API n'y figure JAMAIS : elle est remplacée par `***`.
    """
    methode: str
    url: str
    entetes: dict = Field(default_factory=dict)
    # Paramètres d'URL. Apollo attend ses filtres ici, pas dans le corps.
    params: dict = Field(default_factory=dict)
    corps: dict = Field(default_factory=dict)


class ResultatSource(BaseModel):
    source: str
    # `ok` : appel réussi · `simule` : dry_run · `non_configuree` : clé absente
    # `non_implemente` : connecteur pas encore écrit · `erreur` : appel en échec
    statut: Literal["ok", "simule", "non_configuree", "non_implemente", "erreur"]
    nb_leads: int = 0
    requetes: list[RequeteHTTP] = Field(default_factory=list)
    erreur: Optional[str] = None


class ExecuterPlanRequest(BaseModel):
    workspace_id: UUID
    icp: ICP
    sources: list[str] = Field(default_factory=list)
    zone: Optional[str] = None
    # Nombre maximum de leads ramenés par source (protège la facture).
    limite: int = Field(default=25, ge=1, le=200)
    # True = aucun appel réseau : on renvoie les requêtes qui SERAIENT envoyées.
    # Permet de démontrer et tester la chaîne sans clé d'API ni budget.
    dry_run: bool = True


class ExecuterPlanResponse(BaseModel):
    leads: list[Lead] = Field(default_factory=list)
    par_source: list[ResultatSource] = Field(default_factory=list)
    # Leads écartés parce que leur secteur figure dans `secteurs_exclus`.
    rejetes_hors_icp: int = 0
    diagnostics: list[Diagnostic] = Field(default_factory=list)
