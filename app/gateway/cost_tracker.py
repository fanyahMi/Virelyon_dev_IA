"""Suivi des coûts LLM par workspace **et par agent** (donnée source pour
FINANCE, marge §5.4).

Deux granularités, deux usages :
- **par workspace** — c'est la facture du client, et c'est elle que le plafond
  protège. Un client paie pour son équipe d'agents, pas pour chacun séparément.
- **par agent** — c'est le diagnostic. Sans ce détail, « le workspace a dépassé
  son plafond » ne dit pas si c'est le prospecteur qui ratisse trop large ou le
  relanceur qui s'acharne, et il n'y a rien à corriger.

Stockage en mémoire (indépendant du backend). Note : remis à zéro au redémarrage
— à brancher sur Redis/DB si une persistance longue est requise.
"""
from __future__ import annotations

from collections import defaultdict
from threading import Lock

from app.core.config import prix_pour


class CostLimitExceeded(RuntimeError):
    """Plafond de coût du workspace atteint (→ HTTP 429)."""


def _compteur() -> dict:
    return {"input_tokens": 0, "output_tokens": 0, "cost": 0.0}


class CostTracker:
    def __init__(self) -> None:
        self._data: dict[str, dict] = defaultdict(_compteur)
        # workspace -> agent -> compteur. Les appels sans agent nommé sont
        # rangés sous "-" : ils comptent dans le total, mais ne s'attribuent à
        # personne — les noyer dans un agent réel fausserait son diagnostic.
        self._agents: dict[str, dict[str, dict]] = defaultdict(
            lambda: defaultdict(_compteur)
        )
        self._lock = Lock()

    @staticmethod
    def compute_cost(model: str, in_tok: int, out_tok: int) -> float:
        p_in, p_out = prix_pour(model)
        return in_tok / 1_000_000 * p_in + out_tok / 1_000_000 * p_out

    def record(
        self,
        workspace_id,
        model: str,
        in_tok: int,
        out_tok: int,
        agent_id=None,
    ) -> float:
        cost = self.compute_cost(model, in_tok, out_tok)
        with self._lock:
            for compteur in (
                self._data[str(workspace_id)],
                self._agents[str(workspace_id)][str(agent_id) if agent_id else "-"],
            ):
                compteur["input_tokens"] += in_tok
                compteur["output_tokens"] += out_tok
                compteur["cost"] += cost
        return cost

    def get(self, workspace_id) -> dict:
        """Cumul du workspace — c'est la facture du client."""
        with self._lock:
            return dict(self._data[str(workspace_id)])

    def par_agent(self, workspace_id) -> dict[str, dict]:
        """Détail par agent. Clé « - » = appels sans agent nommé."""
        with self._lock:
            return {a: dict(c) for a, c in self._agents[str(workspace_id)].items()}

    def enforce_limit(self, workspace_id, limit: float) -> None:
        """Lève CostLimitExceeded si le coût cumulé atteint le plafond (limite > 0 = actif).

        Le plafond porte sur le **workspace**, jamais sur l'agent : sinon un client
        ayant deux agents dépenserait deux fois le plafond qu'il croit avoir posé.
        """
        if limit and limit > 0:
            current = self.get(workspace_id)["cost"]
            if current >= limit:
                raise CostLimitExceeded(
                    f"Plafond de coût atteint pour ce workspace "
                    f"({current:.4f} $ ≥ {limit:.4f} $)."
                )

    def clear(self) -> None:
        """Remise à zéro (tests)."""
        with self._lock:
            self._data.clear()
            self._agents.clear()


# instance partagée
cost_tracker = CostTracker()
