"""Endpoints de l'agent APEX (support client). Tous protégés par
l'authentification service-à-service (verify_caller) : seul le backend/n8n
peut appeler."""
from uuid import UUID

from fastapi import APIRouter, Depends

from app.apex import agents, autonomie
from app.apex import config as config_logic
from app.core.security import verify_caller
from app.gateway.router import Gateway, get_gateway
from app.schemas.apex import (
    ClassifyRequest,
    ClassifyResponse,
    ConfigAgentApex,
    DecideActionRequest,
    DecideActionResponse,
    DetecterEscaladeRequest,
    DetecterEscaladeResponse,
    GenerateRequest,
    GenerateResponse,
)

router = APIRouter(prefix="/apex", tags=["apex"], dependencies=[Depends(verify_caller)])


@router.post("/classify", response_model=ClassifyResponse)
async def classify(req: ClassifyRequest, gw: Gateway = Depends(get_gateway)):
    """Compréhension du message + hiérarchisation des fragments (§4.3, prompt §5.1).

    `fragments_candidats` doit déjà contenir le résultat de la recherche
    vectorielle pgvector (fournie par n8n/Big Data) : ce endpoint ne fait
    jamais lui-même de requête à la base de connaissances.
    """
    return await agents.classify(gw, req)


@router.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest, gw: Gateway = Depends(get_gateway)):
    """Génération de réponse, avec sélection d'outil intégrée (§4.4, prompts §5.2/§5.4).

    Si la réponse contient `appel_outil_demande` (et `texte` vide), n8n doit
    exécuter l'outil demandé puis rappeler cet endpoint avec `resultat_outil`
    rempli — APEX ne exécute jamais lui-même un outil (§2.4).
    """
    return await agents.generate(gw, req)


@router.post("/escalade", response_model=DetecterEscaladeResponse)
async def escalade(req: DetecterEscaladeRequest, gw: Gateway = Depends(get_gateway)):
    """Détection de confiance & déclenchement d'escalade (§4.5, prompt §5.3)."""
    return await agents.detecter_escalade(gw, req)


@router.post("/decide-action", response_model=DecideActionResponse)
def decide_action(req: DecideActionRequest):
    """Décision finale de prochaine action — niveaux d'autonomie (§4.14, contrat §5.5).

    Logique pure — aucun appel LLM (voir app/apex/autonomie.py pour la
    justification de ce choix de conception).
    """
    return autonomie.decider_action(req)


@router.get("/config/{workspace_id}", response_model=ConfigAgentApex)
def get_config(workspace_id: UUID):
    """Configuration du client — niveau d'autonomie, ton de voix, outils actifs (§3).

    Un client sans configuration reçoit le défaut prudent (Niveau 1 Supervisé,
    jamais d'envoi automatique) — c'est l'état normal d'un compte qui vient
    d'être créé, pas une erreur.
    """
    return config_logic.charger_config(workspace_id)
