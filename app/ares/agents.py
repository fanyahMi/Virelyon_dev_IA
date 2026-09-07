"""Points de décision d'ARES appelant Claude (via la passerelle).

Chaque fonction : construit l'entrée JSON, appelle le bon tier, valide la sortie
structurée, et joint les métadonnées (modèle, tokens, coût).

Chacune accepte un `agent` — la configuration de l'agent nommé dans la requête,
déjà habilité par `builder/habilitation.py`. Quand il est fourni, **sa
configuration l'emporte** sur ce que l'appelant a joint : c'est ce qui fait que
le prospecteur et le relanceur d'un même client ne parlent pas de la même voix.
`None` = appel sans agent nommé, comportement d'origine.
"""
from __future__ import annotations

import json
from typing import Optional

from app.gateway.provider import ReponseLLMInvalide
from app.gateway.router import Gateway
from app.prompts.ares import (
    CLASSIFY_SYSTEM,
    DECIDE_SYSTEM,
    GENERATE_SYSTEM,
    QUALIFY_SYSTEM,
)
from app.schemas.ares import (
    Action,
    ClassifyRequest,
    ClassifyResponse,
    DecideRequest,
    DecideResponse,
    GenerateRequest,
    GenerateResponse,
    QualifyRequest,
    QualifyResponse,
)
from app.schemas.common import Meta, Usage
from app.schemas.config import ConfigAgent

_ACTION_ALIASES = {
    "continuer": Action.continuer,
    "pause": Action.pause,
    "escalade": Action.escalade,
    "arrêt": Action.arret,
    "arret": Action.arret,
}


def requis(data: dict, cle: str):
    """Champ que le modèle DOIT avoir renvoyé — message explicite s'il manque."""
    if cle not in data:
        raise ReponseLLMInvalide(f"champ « {cle} » absent de la réponse du modèle.")
    return data[cle]


def meta_depuis(info: dict) -> Meta:
    """Métadonnées de réponse à partir du dict rendu par `Gateway.complete_json`."""
    return Meta(
        model_used=info["model_used"],
        usage=Usage(input_tokens=info["input_tokens"], output_tokens=info["output_tokens"]),
        cost_estimate=info["cost"],
        cached=info.get("cached", False),
    )


def dump_json(payload: dict) -> str:
    """Sérialisation unique des messages envoyés au LLM (accents conservés)."""
    return json.dumps(payload, ensure_ascii=False, default=str)


async def qualify(
    gw: Gateway, req: QualifyRequest, agent: Optional[ConfigAgent] = None
) -> QualifyResponse:
    user = dump_json({"lead": req.lead.model_dump(mode="json"), "icp": req.icp.model_dump()})
    data, info = await gw.complete_json(
        "reasoning", QUALIFY_SYSTEM, user, req.workspace_id, agent_id=req.agent_id
    )
    return QualifyResponse(
        qualifie=bool(requis(data, "qualifie")),
        confiance=float(requis(data, "confiance")),
        motif=str(data.get("motif", "")),
        meta=meta_depuis(info),
    )


async def generate(
    gw: Gateway, req: GenerateRequest, agent: Optional[ConfigAgent] = None
) -> GenerateResponse:
    # La voix vient de l'agent quand il y en a un, de la requête sinon.
    ton = agent.ton_de_voix if agent else req.ton_de_voix
    objectif = agent.objectif_principal if agent else req.objectif_principal
    canaux = agent.canaux_actifs if agent else req.canaux_actifs

    user = dump_json(
        {
            "lead": req.lead.model_dump(mode="json"),
            "etape": req.etape,
            "ton_de_voix": ton,
            "historique": [e.model_dump(mode="json") for e in req.historique],
            "langue": req.language,
            "objectif_principal": objectif or None,
            "canaux_autorises": canaux or None,
        }
    )
    data, info = await gw.complete_json(
        "reasoning", GENERATE_SYSTEM, user, req.workspace_id,
        max_tokens=1500, agent_id=req.agent_id,
    )
    # Le modèle est prié de respecter les canaux autorisés, mais on ne s'en
    # remet pas à sa bonne volonté : n8n recevrait un canal non connecté.
    canal = str(data.get("canal", "") or "").strip().lower()
    autorises = [c.lower() for c in canaux]
    if autorises and canal not in autorises:
        canal = autorises[0]
    return GenerateResponse(
        texte=str(requis(data, "texte")),
        canal=canal or "email",
        meta=meta_depuis(info),
    )


async def classify(
    gw: Gateway, req: ClassifyRequest, agent: Optional[ConfigAgent] = None
) -> ClassifyResponse:
    user = dump_json({"message": req.message_entrant, "langue": req.language})
    data, info = await gw.complete_json(
        "fast", CLASSIFY_SYSTEM, user, req.workspace_id, agent_id=req.agent_id
    )
    return ClassifyResponse(
        categorie=str(requis(data, "categorie")),
        confiance=float(requis(data, "confiance")),
        date_relance=data.get("date_relance"),
        meta=meta_depuis(info),
    )


async def decide(
    gw: Gateway, req: DecideRequest, agent: Optional[ConfigAgent] = None
) -> DecideResponse:
    # Garde-fou DÉTERMINISTE (§4.3.1 / §5.4) : plafond de relance atteint → arrêt,
    # sans appel LLM (gratuit, instantané, respecte toujours le plafond).
    if req.relances_effectuees >= req.palier.relances_max:
        return DecideResponse(
            action=Action.arret,
            justification=(
                f"Plafond de relance atteint ({req.relances_effectuees}/"
                f"{req.palier.relances_max}, palier {req.palier.nom}) — arrêt de la séquence."
            ),
            meta=None,
        )

    user = dump_json(
        {
            "lead": req.lead.model_dump(mode="json"),
            "palier": req.palier.model_dump(),
            "relances_effectuees": req.relances_effectuees,
            "contexte": req.contexte,
        }
    )
    data, info = await gw.complete_json(
        "reasoning", DECIDE_SYSTEM, user, req.workspace_id, agent_id=req.agent_id
    )
    action = _ACTION_ALIASES.get(str(requis(data, "action")).strip().lower())
    if action is None:
        raise ValueError(f"action inconnue: {data.get('action')!r}")
    return DecideResponse(action=action, justification=str(data.get("justification", "")), meta=meta_depuis(info))
