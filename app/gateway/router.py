"""Passerelle IA : traduit une capacité (tier) en modèle Claude, appelle le
provider, suit les coûts, et extrait le JSON de la réponse.

L'agent demande un `tier` ("fast"/"reasoning"), jamais un modèle précis — ce qui
permet de changer de modèle sans toucher aux agents.
"""
from __future__ import annotations

import json
import re

from fastapi import Depends

from app.core.config import get_settings, modele_pour
from app.gateway.cache import response_cache
from app.gateway.cost_tracker import cost_tracker
from app.gateway.provider import LLMProvider, ReponseLLMInvalide, get_provider


def _extract_json(text: str) -> dict:
    """Extrait un objet JSON d'une réponse LLM (tolère les ``` et le texte autour)."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    raw = match.group(0) if match else text
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReponseLLMInvalide(f"JSON illisible dans la réponse du modèle : {exc}") from exc


class Gateway:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def complete_json(
        self, tier: str, system: str, user: str, workspace_id, max_tokens: int | None = None
    ) -> tuple[dict, dict]:
        settings = get_settings()
        model = modele_pour(tier)
        mt = max_tokens or settings.default_max_tokens

        # Cache (§5.4) : un appel identique déjà vu est renvoyé gratuitement.
        key = response_cache.make_key(workspace_id, tier, system, user, mt)
        cached = response_cache.get(key)
        if cached is not None:
            info = {"model_used": model, "input_tokens": 0, "output_tokens": 0,
                    "cost": 0.0, "cached": True}
            return cached, info

        # Miss → on vérifie le plafond de coût AVANT l'appel payant (§8.5).
        cost_tracker.enforce_limit(workspace_id, settings.max_cost_per_workspace)
        text, in_tok, out_tok = await self.provider.generate(model, system, user, mt)
        cost = cost_tracker.record(workspace_id, model, in_tok, out_tok)
        data = _extract_json(text)
        response_cache.set(key, data)
        info = {
            "model_used": model,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost": cost,
            "cached": False,
        }
        return data, info


def get_gateway(provider: LLMProvider = Depends(get_provider)) -> Gateway:
    return Gateway(provider)
