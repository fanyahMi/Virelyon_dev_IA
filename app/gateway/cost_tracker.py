"""Suivi des coûts LLM par workspace (donnée source pour FINANCE, marge §5.4).

Stockage en mémoire (indépendant du backend). Note : remis à zéro au redémarrage
— à brancher sur Redis/DB si une persistance longue est requise.
"""
from collections import defaultdict
from threading import Lock

from app.core.config import prix_pour


class CostLimitExceeded(RuntimeError):
    """Plafond de coût du workspace atteint (→ HTTP 429)."""


class CostTracker:
    def __init__(self) -> None:
        self._data: dict[str, dict] = defaultdict(
            lambda: {"input_tokens": 0, "output_tokens": 0, "cost": 0.0}
        )
        self._lock = Lock()

    @staticmethod
    def compute_cost(model: str, in_tok: int, out_tok: int) -> float:
        p_in, p_out = prix_pour(model)
        return in_tok / 1_000_000 * p_in + out_tok / 1_000_000 * p_out

    def record(self, workspace_id, model: str, in_tok: int, out_tok: int) -> float:
        cost = self.compute_cost(model, in_tok, out_tok)
        with self._lock:
            d = self._data[str(workspace_id)]
            d["input_tokens"] += in_tok
            d["output_tokens"] += out_tok
            d["cost"] += cost
        return cost

    def get(self, workspace_id) -> dict:
        with self._lock:
            return dict(self._data[str(workspace_id)])

    def enforce_limit(self, workspace_id, limit: float) -> None:
        """Lève CostLimitExceeded si le coût cumulé atteint le plafond (limite > 0 = actif)."""
        if limit and limit > 0:
            current = self.get(workspace_id)["cost"]
            if current >= limit:
                raise CostLimitExceeded(
                    f"Plafond de coût atteint pour ce workspace "
                    f"({current:.4f} $ ≥ {limit:.4f} $)."
                )


# instance partagée
cost_tracker = CostTracker()
