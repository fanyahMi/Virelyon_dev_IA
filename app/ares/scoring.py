"""Scoring & priorisation (§4.3) + paliers de relance (§4.3.1).

Logique PURE (aucun appel LLM) — donc testable unitairement sans réseau ni clé.

Quand un agent est nommé, **ses** pondérations et **ses** paliers s'appliquent :
un prospecteur qui ratisse peut valoriser la complétude, un relanceur qui doit
choisir sur qui s'acharner valorisera l'engagement. Les constantes ci-dessous ne
servent plus qu'aux appels sans agent nommé.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.schemas.ares import ICP, Lead, Palier, ScoreRequest, ScoreResponse
from app.schemas.config import ConfigAgent

# Paliers (seuil, palier) triés du plus haut au plus bas — CDCF §4.3.1
_PALIERS: list[tuple[int, Palier]] = [
    (95, Palier(nom="quasi_parfait", relances_max=5, cadence=[0, 3, 7, 12, 18, 25])),
    (90, Palier(nom="tres_forte", relances_max=4, cadence=[0, 3, 8, 15, 22])),
    (70, Palier(nom="correcte", relances_max=3, cadence=[0, 4, 10, 18])),
    (0, Palier(nom="faible", relances_max=1, cadence=[0, 7])),
]


def _fit(lead: Lead, icp: ICP) -> float:
    """Force de correspondance avec l'ICP (0-1). Secteur exclu => 0 (hors-ICP)."""
    matched = 0.0
    checks = 0
    if icp.secteurs_inclus or icp.secteurs_exclus:
        checks += 1
        secteur = (lead.secteur or "").lower()
        if secteur and secteur in [s.lower() for s in icp.secteurs_exclus]:
            return 0.0
        if not icp.secteurs_inclus or (secteur and secteur in [s.lower() for s in icp.secteurs_inclus]):
            matched += 1
    if icp.taille_min is not None or icp.taille_max is not None:
        checks += 1
        lo = icp.taille_min if icp.taille_min is not None else 0
        hi = icp.taille_max if icp.taille_max is not None else 10**9
        if lead.taille_effectif is not None and lo <= lead.taille_effectif <= hi:
            matched += 1
    if icp.roles_cibles:
        checks += 1
        role = (lead.role_contact or "").lower()
        if role and any(role == r.lower() for r in icp.roles_cibles):
            matched += 1
    return matched / checks if checks else 0.5


def _completude(lead: Lead) -> float:
    email = lead.contact.get("email") if lead.contact else None
    fields = [lead.secteur, lead.taille_effectif, lead.role_contact, email, lead.montant_potentiel]
    present = sum(1 for f in fields if f not in (None, "", {}, []))
    return present / len(fields)


def _fraicheur(lead: Lead) -> float:
    if not lead.ingested_at:
        return 0.5
    dt = lead.ingested_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - dt).days
    if days <= 7:
        return 1.0
    if days <= 30:
        return 0.7
    if days <= 90:
        return 0.4
    return 0.2


def _engagement(lead: Lead) -> float:
    signaux = (lead.donnees_brutes or {}).get("signaux_bruts") or []
    return min(1.0, 0.3 * len(signaux))


def compute_score(
    req: ScoreRequest, agent: Optional[ConfigAgent] = None
) -> ScoreResponse:
    cfg = agent.scoring_config if agent else req.scoring_config
    comp = {
        "fraicheur": _fraicheur(req.lead),
        "completude": _completude(req.lead),
        "fit": _fit(req.lead, req.icp),
        "engagement": _engagement(req.lead),
    }
    total_poids = (
        cfg.poids_fraicheur + cfg.poids_completude + cfg.poids_fit + cfg.poids_engagement
    ) or 1.0
    raw = (
        cfg.poids_fraicheur * comp["fraicheur"]
        + cfg.poids_completude * comp["completude"]
        + cfg.poids_fit * comp["fit"]
        + cfg.poids_engagement * comp["engagement"]
    )
    score = max(0, min(100, round(100 * raw / total_poids)))
    # `agent.paliers` est toujours renseigné par le chargement de config, mais on
    # ne le suppose pas : un palier manquant ferait planter une note parfaitement
    # calculée, là où les paliers du CDCF sont un repli acceptable.
    if agent and agent.paliers:
        palier = agent.palier_pour(score)
    else:
        palier = next(p for seuil, p in _PALIERS if score >= seuil)
    breakdown = {**{k: round(v, 3) for k, v in comp.items()}, "poids": cfg.model_dump()}
    return ScoreResponse(score=score, breakdown=breakdown, palier=palier)
