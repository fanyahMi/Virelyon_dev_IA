"""Contrats d'entrée/sortie (API) de l'agent APEX — support client.

Même principe que app/schemas/ares.py : service STATELESS. Le backend/n8n passe
tout ce qui est nécessaire dans la requête (message, historique, fragments déjà
retrouvés par recherche vectorielle, configuration du workspace) ; APEX ne touche
JAMAIS pgvector ni la base de données directement — c'est Big Data / n8n qui
alimentent et interrogent `apex_knowledge_chunks` (CDCF APEX v2.0 §1.3, §4.1).
Les fragments candidats arrivent donc ici exactement comme `lead.donnees_brutes`
arrive côté ARES : déjà collectés, jamais recalculés par ce service.
"""
from enum import Enum
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import Meta


# ----- Enums fermées (glossaire CDCF APEX v2.0 §11) -----
class Intention(str, Enum):
    question_produit = "question_produit"
    reclamation = "reclamation"
    hors_scope = "hors_scope"
    demande_humain = "demande_humain"


class NiveauAutonomie(str, Enum):
    supervise = "supervise"
    semi_autonome = "semi_autonome"
    autonome = "autonome"


class DecisionEscalade(str, Enum):
    """Sortie du Module 4.5 (détection de confiance) — alimente ensuite le
    Module 4.14 (niveaux d'autonomie, §5.5)."""

    continuer = "continuer"
    brouillon = "brouillon"
    escalade = "escalade"


class ActionAgent(str, Enum):
    """Sortie finale (§4.14 / §5.5) : ce que la conversation doit réellement faire."""

    repondre = "repondre"
    brouillon = "brouillon"
    escalade = "escalade"
    cloturer = "cloturer"


class Sentiment(str, Enum):
    positif = "positif"
    neutre = "neutre"
    negatif = "negatif"


class TypeRegleEscalade(str, Enum):
    mot_cle = "mot_cle"
    sentiment_negatif = "sentiment_negatif"
    nb_echanges_sans_resolution = "nb_echanges_sans_resolution"
    hors_perimetre_connaissance = "hors_perimetre_connaissance"
    tentative_contournement = "tentative_contournement"


# ----- Objets de domaine (passés par le backend/n8n) -----
class FragmentCandidat(BaseModel):
    """Fragment déjà remonté par la recherche vectorielle pgvector (Big Data/n8n).

    APEX ne touche jamais pgvector : cette liste arrive prête à l'emploi, comme
    `lead.donnees_brutes` côté ARES (CDCF §1.3).
    """

    chunk_texte: str
    document_id: Optional[UUID] = None
    position_dans_document: Optional[int] = None
    score: float = Field(ge=0, le=1, description="score brut de similarité pgvector")


class FragmentPertinent(BaseModel):
    """Fragment retenu après hiérarchisation par Claude (§4.3, re-classement)."""

    chunk_texte: str
    document_id: Optional[UUID] = None
    score: float = Field(ge=0, le=1, description="score de pertinence après re-classement")


class MessageHistorique(BaseModel):
    """Un échange déjà eu dans cette conversation — structuré pour garder qui a
    parlé et quand (même logique que EchangeHistorique côté ARES)."""

    texte: str
    emetteur: Literal["client_final", "agent", "humain"] = "client_final"
    date: Optional[str] = None


class ResultatOutil(BaseModel):
    """Résultat d'un outil déjà EXÉCUTÉ par n8n (ex. get_customer_context), à
    fournir à `/apex/generate` pour enrichir la réponse. APEX ne l'exécute
    jamais lui-même — §2.4 : « les outils sont des appels HTTP simples
    orchestrés par n8n »."""

    nom_outil: Literal["get_customer_context"]
    donnees: dict = Field(default_factory=dict)


class AppelOutilDemande(BaseModel):
    """Décision d'appeler un outil, prise par Claude PENDANT `/apex/generate`
    (§4.4 « sélection d'outil intégrée », contrat détaillé en §5.4). APEX ne
    fait QUE décider — c'est à n8n d'exécuter l'appel puis de rappeler
    `/apex/generate` avec `resultat_outil` rempli. Aucune écriture n'a jamais
    lieu ici (§2.4 : « aucun outil n'écrit jamais... sans action explicitement
    prévue »)."""

    nom_outil: Literal["get_customer_context"]
    parametres: dict = Field(default_factory=dict)


class RegleEscalade(BaseModel):
    """Un déclencheur d'escalade configuré côté client (table `regles_escalade`)."""

    type_regle: TypeRegleEscalade
    valeur_seuil: dict = Field(default_factory=dict)
    actif: bool = True


# ----- Configuration du workspace (agent_config apex, §3 / §4.14) -----
class ConfigAgentApex(BaseModel):
    """Tout ce qu'APEX doit savoir du client pour se comporter comme lui."""

    workspace_id: UUID
    statut: Literal["configuration_incomplete", "inactif", "actif"] = "configuration_incomplete"
    ton_de_voix: Literal["formel", "amical"] = "formel"
    langue: str = "fr"
    canaux_actifs: list[str] = Field(default_factory=lambda: ["chat_web"])
    quotas_par_canal: dict = Field(default_factory=dict)
    # Niveau 1 par défaut pour tout nouveau workspace — jamais autonome sans
    # choix explicite du client (CDCF §4.14, « États & limites »).
    niveau_autonomie: NiveauAutonomie = NiveauAutonomie.supervise
    outils_actifs: list[str] = Field(default_factory=list)
    seuil_pertinence: float = Field(default=0.5, ge=0, le=1)
    # Au-delà de ce seuil, le Niveau 2 (semi-autonome) répond seul à une
    # intention question_produit (CDCF §4.14).
    seuil_confiance_semi_autonome: float = Field(default=0.8, ge=0, le=1)
    regles_escalade: list[RegleEscalade] = Field(default_factory=list)


# ----- Compréhension & recherche contextuelle (§4.3 / prompt §5.1) -----
class ClassifyRequest(BaseModel):
    workspace_id: UUID
    message_entrant: str = Field(min_length=1)
    historique: list[MessageHistorique] = Field(default_factory=list)
    # Déjà retrouvés par pgvector côté n8n/Big Data — jamais recalculés ici.
    fragments_candidats: list[FragmentCandidat] = Field(default_factory=list)
    langue_workspace: str = "fr"
    # Seuil configuré côté workspace (agent_config.seuil_pertinence).
    seuil_pertinence: float = Field(default=0.5, ge=0, le=1)


class ClassifyResult(BaseModel):
    intention: Intention
    fragments_pertinents: list[FragmentPertinent] = Field(default_factory=list)
    confiance: float = Field(ge=0, le=1)
    langue_detectee: str = "fr"
    # Calculé en PYTHON, jamais laissé au seul jugement du modèle — garde-fou
    # anti-hallucination non négociable (§4.3) : aucun fragment au-dessus du
    # seuil configuré => True, quelle que soit la confiance annoncée par Claude.
    necessite_escalade: bool = False


class ClassifyResponse(ClassifyResult):
    meta: Optional[Meta] = None


# ----- Génération de réponse + sélection d'outil intégrée -----
# (§4.4 / prompt §5.2, embarque le contrat §5.4 — voir app/prompts/apex.py)
class GenerateRequest(BaseModel):
    workspace_id: UUID
    fragments_pertinents: list[FragmentPertinent] = Field(default_factory=list)
    historique: list[MessageHistorique] = Field(default_factory=list)
    ton_de_voix: Literal["formel", "amical"] = "formel"
    langue: str = "fr"
    outils_actifs: list[str] = Field(default_factory=list)
    # Résultat d'un outil déjà exécuté par n8n lors d'un appel précédent — voir
    # AppelOutilDemande. Absent = Claude peut encore demander un outil.
    resultat_outil: Optional[ResultatOutil] = None
    # Seuil configuré côté workspace (agent_config.seuil_pertinence). Revérifié
    # ICI, en Python — règle absolue du CDCF : « APEX ne doit JAMAIS répondre à
    # partir de ses connaissances générales », implémentée en code, pas
    # seulement dans le prompt (défense en profondeur avec /apex/classify, qui
    # applique déjà ce même seuil — voir app/apex/agents.py::generate).
    seuil_pertinence: float = Field(default=0.5, ge=0, le=1)


class GenerateResult(BaseModel):
    # Vide quand Claude préfère demander un outil avant de répondre
    # (`appel_outil_demande` rempli), ou quand le garde-fou anti-hallucination
    # a bloqué toute génération (`necessite_escalade=True`).
    texte: str = ""
    confiance: float = Field(ge=0, le=1)
    justification: str = ""
    # Calculé en PYTHON (jamais laissé au modèle) : aucun fragment au-dessus du
    # seuil configuré => True, et dans ce cas aucun appel LLM n'a eu lieu.
    necessite_escalade: bool = False
    appel_outil_demande: Optional[AppelOutilDemande] = None


class GenerateResponse(GenerateResult):
    meta: Optional[Meta] = None


# ----- Détection de confiance & escalade (§4.5 / prompt §5.3) -----
class DetecterEscaladeRequest(BaseModel):
    workspace_id: UUID
    message_entrant: str = Field(min_length=1)
    historique: list[MessageHistorique] = Field(default_factory=list)
    intention: Optional[Intention] = None
    regles_escalade: list[RegleEscalade] = Field(default_factory=list)
    nb_echanges_sans_resolution: int = 0
    tentatives_contournement_precedentes: int = 0
    langue: str = "fr"


class DetecterEscaladeResult(BaseModel):
    sentiment: Sentiment = Sentiment.neutre
    declencheurs_actifs: list[TypeRegleEscalade] = Field(default_factory=list)
    tentative_contournement: bool = False
    decision: DecisionEscalade
    justification: str = ""


class DetecterEscaladeResponse(DetecterEscaladeResult):
    # meta=None quand la décision est déterministe (demande humaine explicite,
    # plafond d'échanges atteint, récidive de contournement) — aucun appel LLM.
    meta: Optional[Meta] = None


# ----- Décision finale de prochaine action -----
# (transverse §4.5/§4.6/§4.14, contrat §5.5) — logique PURE, voir app/apex/autonomie.py
class DecideActionRequest(BaseModel):
    niveau_autonomie: NiveauAutonomie
    intention: Intention
    confiance: float = Field(ge=0, le=1)
    decision_escalade: DecisionEscalade
    seuil_confiance_semi_autonome: float = Field(default=0.8, ge=0, le=1)
    # Un humain a cliqué « marquer résolu » — l'emporte sur toute autre règle.
    cloture_demandee: bool = False


class DecideActionResponse(BaseModel):
    action: ActionAgent
    justification: str
