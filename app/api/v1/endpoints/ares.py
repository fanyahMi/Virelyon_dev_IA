"""Endpoints de l'agent ARES. Tous protégés par l'authentification service-à-service
(verify_caller) : seul le backend peut appeler.

Chaque route résout d'abord l'**habilitation** : si la requête nomme un `agent_id`,
le service vérifie qu'il appartient au workspace, qu'il est actif et que son rôle
autorise l'acte demandé — puis applique sa configuration. Sans `agent_id`, le
comportement d'origine est conservé (le backend fournit tout dans le corps).
"""
from fastapi import APIRouter, Depends

from app.ares import agents, scoring
from app.builder import habilitation
from app.core.security import verify_caller
from app.gateway.router import Gateway, get_gateway
from app.schemas.ares import (
    ClassifyRequest,
    ClassifyResponse,
    DecideRequest,
    DecideResponse,
    GenerateRequest,
    GenerateResponse,
    QualifyRequest,
    QualifyResponse,
    ScoreRequest,
    ScoreResponse,
)

router = APIRouter(prefix="/ares", tags=["ares"], dependencies=[Depends(verify_caller)])


@router.post("/qualify", response_model=QualifyResponse)
async def qualify(req: QualifyRequest, gw: Gateway = Depends(get_gateway)):
    agent = habilitation.resoudre(req.workspace_id, req.agent_id, "qualify")
    return await agents.qualify(gw, req, agent)


@router.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest, gw: Gateway = Depends(get_gateway)):
    agent = habilitation.resoudre(req.workspace_id, req.agent_id, "generate")
    return await agents.generate(gw, req, agent)


@router.post("/classify", response_model=ClassifyResponse)
async def classify(req: ClassifyRequest, gw: Gateway = Depends(get_gateway)):
    # Réservé au relanceur : lire une réponse suppose une conversation ouverte.
    agent = habilitation.resoudre(req.workspace_id, req.agent_id, "classify")
    return await agents.classify(gw, req, agent)


@router.post("/decide", response_model=DecideResponse)
async def decide(req: DecideRequest, gw: Gateway = Depends(get_gateway)):
    # Garde-fou déterministe (plafond de relance) sans LLM ; sinon décision via Claude.
    agent = habilitation.resoudre(req.workspace_id, req.agent_id, "decide")
    return await agents.decide(gw, req, agent)


@router.post("/score", response_model=ScoreResponse)
async def score(req: ScoreRequest):
    # Logique pure — pas d'appel LLM.
    agent = habilitation.resoudre(req.workspace_id, req.agent_id, "score")
    return scoring.compute_score(req, agent)
