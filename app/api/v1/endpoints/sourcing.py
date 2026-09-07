"""Endpoint de sourcing — exécute un plan de recherche sur les sources externes.

Par défaut en **mode simulation** (`dry_run: true`) : aucune requête n'est envoyée,
aucune facture. On voit exactement ce qui serait appelé. C'est ce qui permet de
valider et démontrer la chaîne avant d'avoir les clés d'API.
"""
from fastapi import APIRouter, Depends

from app.builder import habilitation
from app.core.security import verify_caller
from app.schemas.sourcing import ExecuterPlanRequest, ExecuterPlanResponse
from app.sourcing.executeur import executer_plan

router = APIRouter(prefix="/sourcing", tags=["sourcing"], dependencies=[Depends(verify_caller)])


@router.post("/executer", response_model=ExecuterPlanResponse)
async def executer(req: ExecuterPlanRequest):
    """ICP → recherche sur les sources → leads bruts, prêts pour `/ares/qualify`.

    Le secteur de chaque lead est canonisé à la collecte : sans ça, le filtrage
    ICP échouerait sur des leads parfaitement valides.

    Capacité réservée au **prospecteur** : c'est le seul poste dont le métier est
    d'aller chercher des entreprises qu'on ne connaît pas encore.
    """
    habilitation.resoudre(req.workspace_id, req.agent_id, "sourcing")
    return await executer_plan(req)
