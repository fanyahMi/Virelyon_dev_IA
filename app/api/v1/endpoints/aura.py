"""Endpoints IA d'AURA (reporting & analytics). Tous protégés par l'authentification
service-à-service (verify_caller) : seul le backend peut appeler.

Stateless, comme ARES/APEX : tout arrive dans le corps de la requête (KPI et
agrégats DÉJÀ calculés et validés par le backend). Ces endpoints n'accèdent
jamais à PostgreSQL/Supabase ni à aucune source (HubSpot, Salesforce, Stripe…) :
`workspace_id` ne sert qu'à isoler le cache et le suivi des coûts.

Erreurs (convention du service, voir app/main.py) : corps invalide → 422 ;
aucun indicateur à raconter → 422 ; plafond de coût ou fournisseur surchargé →
429 ; fournisseur indisponible ou non configuré → 503 ; réponse du modèle
illisible ou non conforme au contrat → 502. Jamais de réponse vide à la place
d'une erreur.
"""
from fastapi import APIRouter, Depends

from app.aura import service
from app.aura.schemas import (
    IndicatorAvailabilityInput,
    IndicatorAvailabilityResponse,
    NarrativeInput,
    NarrativeResponse,
    PerformanceInput,
    PerformanceResponse,
)
from app.core.security import verify_caller
from app.gateway.router import Gateway, get_gateway

router = APIRouter(prefix="/aura", tags=["aura"], dependencies=[Depends(verify_caller)])


@router.post("/narrative", response_model=NarrativeResponse)
async def narrative(req: NarrativeInput, gw: Gateway = Depends(get_gateway)):
    """Narration d'un rapport à partir d'indicateurs déjà calculés."""
    return await service.generate_narrative(gw, req)


@router.post("/performance", response_model=PerformanceResponse)
async def performance(req: PerformanceInput, gw: Gateway = Depends(get_gateway)):
    """Bulletin de performance ARES/APEX à partir d'agrégats déjà calculés."""
    return await service.generate_performance_bulletin(gw, req)


@router.post("/indicator-availability", response_model=IndicatorAvailabilityResponse)
async def indicator_availability(
    req: IndicatorAvailabilityInput, gw: Gateway = Depends(get_gateway)
):
    """Un indicateur est-il calculable, d'après les sources décrites par le backend ?"""
    return await service.check_indicator_availability(gw, req)
