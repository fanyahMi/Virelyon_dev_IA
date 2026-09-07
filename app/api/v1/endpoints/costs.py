"""Consultation des coûts LLM cumulés par workspace (pour FINANCE)."""
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.security import verify_caller
from app.gateway.cost_tracker import cost_tracker

router = APIRouter(prefix="/costs", tags=["costs"], dependencies=[Depends(verify_caller)])


@router.get("/{workspace_id}")
def get_costs(workspace_id: UUID) -> dict:
    """Facture du client, et son détail par agent.

    Le total reste la seule valeur qui engage : c'est lui que le plafond protège.
    `par_agent` sert au diagnostic — savoir *lequel* des agents a dépensé, sans
    quoi un dépassement ne dit pas quoi corriger. La clé « - » regroupe les
    appels passés sans agent nommé.
    """
    return {
        "workspace_id": str(workspace_id),
        **cost_tracker.get(workspace_id),
        "par_agent": cost_tracker.par_agent(workspace_id),
    }
