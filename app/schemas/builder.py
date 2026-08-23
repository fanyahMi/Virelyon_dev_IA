"""Contrats de l'Agent Builder — ce que le backend envoie et reçoit pour l'écran
de paramétrage (objectif, ICP, canaux, ton de voix).

Le service IA ne persiste rien : il transforme et vérifie, le backend enregistre.
"""
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.ares import ICP
from app.schemas.common import Meta


# ----- Référentiels (alimente les listes déroulantes du front) -----
class Referentiels(BaseModel):
    """Catalogue de SUGGESTIONS pour les listes déroulantes.

    Ce n'est pas une liste fermée : un client peut cibler un secteur absent
    du catalogue, il sera canonisé et accepté.
    """
    secteurs: list[str]
    secteurs_services_b2b: list[str]
    secteurs_autres: list[str]
    roles: list[str]
    tons_de_voix: list[str]
    canaux: list[str]


# ----- Diagnostic partagé -----
class Diagnostic(BaseModel):
    """Un problème détecté sur l'ICP.

    `erreur` = bloquant (l'ICP ne peut pas fonctionner tel quel).
    `avertissement` = l'ICP fonctionne mais donnera probablement de mauvais résultats.
    `info` = rien d'anormal, simple point d'attention (ex. secteur personnalisé).
    """
    niveau: Literal["erreur", "avertissement", "info"]
    champ: str
    message: str
    suggestion: Optional[str] = None


def diag(
    niveau: Literal["erreur", "avertissement", "info"],
    champ: str,
    message: str,
    suggestion: Optional[str] = None,
) -> Diagnostic:
    """Raccourci de construction — les diagnostics sont nombreux et verbeux inline."""
    return Diagnostic(niveau=niveau, champ=champ, message=message, suggestion=suggestion)


# ----- Extraction d'ICP depuis du texte libre -----
class ICPExtraireRequest(BaseModel):
    """Le client décrit sa cible en langage normal ; on en tire un ICP structuré."""
    workspace_id: UUID
    texte: str = Field(min_length=3)
    language: str = "fr"


class ICPExtraireResponse(BaseModel):
    icp: ICP
    confiance: float = Field(ge=0, le=1)
    # Termes de la phrase qui n'ont pas pu être rattachés au référentiel :
    # à afficher au client pour qu'il complète à la main.
    non_reconnu: list[str] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    meta: Meta


# ----- Validation d'un ICP (logique pure, aucun appel LLM) -----
class ICPValiderRequest(BaseModel):
    icp: ICP


class ICPValiderResponse(BaseModel):
    # True si aucun diagnostic de niveau "erreur".
    valide: bool
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    # Nombre de critères réellement discriminants (0 à 3) : secteur, taille, rôle.
    # 0 = l'ICP ne filtre rien, tous les leads obtiennent le même fit.
    criteres_actifs: int


# ----- Plan de recherche (ICP → requêtes exploitables par source) -----
# Sources de DÉCOUVERTE : trouvent des entreprises qu'on ne connaît pas encore.
SOURCES_DECOUVERTE = ("google_maps", "apollo", "linkedin", "openstreetmap")
# Sources d'ENRICHISSEMENT : complètent une entreprise DÉJÀ trouvée.
SOURCES_ENRICHISSEMENT = ("site_web", "hunter")
SOURCES_CONNUES = SOURCES_DECOUVERTE + SOURCES_ENRICHISSEMENT


class BlocRecherche(BaseModel):
    """Instructions prêtes à consommer pour une source."""
    source: str
    # "requetes_texte" (Maps) · "filtres" (Apollo, LinkedIn)
    # "extraction" (site web) · "domain_search" (Hunter)
    type: str
    requetes: list[str] = Field(default_factory=list)
    filtres: dict = Field(default_factory=dict)
    champs_cibles: list[str] = Field(default_factory=list)
    # Réserve juridique ou opérationnelle sur cette source, à remonter au client.
    avertissement: Optional[str] = None


class PlanRechercheRequest(BaseModel):
    workspace_id: UUID
    icp: ICP
    # Sources activées par le client. Vide = toutes celles qui sont exploitables.
    sources: list[str] = Field(default_factory=list)
    # Zone géographique — fournie EXPLICITEMENT par le client, jamais déduite
    # (contrainte CDCF §8 : aucun ciblage géographique en dur).
    zone: Optional[str] = None


class PlanRechercheResponse(BaseModel):
    decouverte: list[BlocRecherche] = Field(default_factory=list)
    enrichissement: list[BlocRecherche] = Field(default_factory=list)
    # Secteurs à écarter APRÈS collecte : aucune source externe ne sait exclure.
    secteurs_exclus: list[str] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
