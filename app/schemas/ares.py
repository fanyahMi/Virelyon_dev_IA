"""Contrats d'entrée/sortie (API) de l'agent ARES.

Principe : service STATELESS. Le backend passe tout ce qui est nécessaire dans
la requête (lead, ICP, config) ; l'IA ne lit jamais la base de données.
"""
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import Meta


# ----- Objets de domaine (passés par le backend) -----
class Lead(BaseModel):
    nom: str
    secteur: Optional[str] = None
    taille_effectif: Optional[int] = None
    role_contact: Optional[str] = None
    contact: dict = Field(default_factory=dict)
    montant_potentiel: Optional[float] = None
    donnees_brutes: dict = Field(default_factory=dict)  # fourni par Big Data
    ingested_at: Optional[datetime] = None
    langue: Optional[str] = None


class ICP(BaseModel):
    """Profil client idéal — source unique côté workspace (P0-01)."""
    secteurs_inclus: list[str] = Field(default_factory=list)
    secteurs_exclus: list[str] = Field(default_factory=list)
    taille_min: Optional[int] = None
    taille_max: Optional[int] = None
    roles_cibles: list[str] = Field(default_factory=list)


class ScoringConfig(BaseModel):
    """Pondérations configurables par workspace (jamais figées dans le code)."""
    poids_fraicheur: float = 0.25
    poids_completude: float = 0.25
    poids_fit: float = 0.40
    poids_engagement: float = 0.10


class Palier(BaseModel):
    nom: str
    relances_max: int
    cadence: list[int]  # jours : [0, 3, 7, ...]


# ----- Qualification (§4.2 / prompt §5.1) -----
class QualifyRequest(BaseModel):
    workspace_id: UUID
    lead: Lead
    icp: ICP


class QualifyResult(BaseModel):
    qualifie: bool
    confiance: float = Field(ge=0, le=1)
    motif: str


class QualifyResponse(QualifyResult):
    meta: Meta


# ----- Scoring (§4.3 / §4.3.1) — logique pure -----
class ScoreRequest(BaseModel):
    workspace_id: UUID
    lead: Lead
    icp: ICP
    scoring_config: ScoringConfig = Field(default_factory=ScoringConfig)


class ScoreResponse(BaseModel):
    score: int = Field(ge=0, le=100)
    breakdown: dict
    palier: Palier


# ----- Génération de message (§4.5 / prompt §5.2) -----
class EchangeHistorique(BaseModel):
    """Un échange déjà eu avec ce prospect.

    Structuré plutôt qu'une simple chaîne : pour rédiger une relance juste, le
    modèle doit savoir qui a parlé, quand et sur quel canal — « je reviens vers
    vous après notre échange du 15 » n'est possible qu'avec la date.
    """
    texte: str
    role: str = "ares"  # "ares" ou "prospect"
    canal: Optional[str] = None
    date: Optional[str] = None


class GenerateRequest(BaseModel):
    workspace_id: UUID
    lead: Lead
    etape: str = "J0"
    ton_de_voix: str = "professionnel"
    historique: list[EchangeHistorique] = Field(default_factory=list)
    language: str = "fr"
    # Mission que le client a décrite dans l'Agent Builder. Sans elle, deux
    # clients différents obtiennent des messages quasi identiques.
    objectif_principal: str = ""
    # Canaux que le client a réellement connectés. Vide = pas de contrainte.
    # Sans ça, le modèle peut proposer un canal indisponible.
    canaux_actifs: list[str] = Field(default_factory=list)


class GenerateResult(BaseModel):
    texte: str
    canal: str


class GenerateResponse(GenerateResult):
    meta: Meta


# ----- Classification de réponse (§4.6 / prompt §5.3) -----
class ClassifyRequest(BaseModel):
    workspace_id: UUID
    message_entrant: str
    language: str = "fr"


class ClassifyResult(BaseModel):
    categorie: str
    confiance: float = Field(ge=0, le=1)
    date_relance: Optional[str] = None


class ClassifyResponse(ClassifyResult):
    meta: Meta


# ----- Décision de prochaine action (transverse §4.4/4.6/4.7 / prompt §5.4) -----
class Action(str, Enum):
    continuer = "continuer"
    pause = "pause"
    escalade = "escalade"
    arret = "arrêt"


class DecideRequest(BaseModel):
    workspace_id: UUID
    lead: Lead
    palier: Palier                 # palier de score du lead (§4.3.1)
    relances_effectuees: int = 0   # compteur de relances déjà envoyées (v1.1)
    contexte: Optional[str] = None  # ex : dernière interaction / événement déclencheur


class DecideResult(BaseModel):
    action: Action
    justification: str


class DecideResponse(DecideResult):
    # meta = None quand la décision est déterministe (plafond atteint, aucun appel LLM)
    meta: Optional[Meta] = None
