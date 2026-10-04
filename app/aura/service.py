"""Comportement IA d'AURA : narration, bulletin ARES/APEX, disponibilité d'indicateur.

Même principe que app/ares/agents.py et app/apex/agents.py : chaque fonction
construit l'entrée JSON, appelle le Gateway EXISTANT (tier, coût, cache, erreurs
HTTP gérés par lui — aucun second client LLM), puis valide la sortie.

AURA ne calcule rien et ne lit aucune base : le backend fournit des données déjà
validées. Une réponse non conforme est une ERREUR TECHNIQUE (`ReponseLLMInvalide`,
→ HTTP 502) : on ne la « répare » jamais en la interprétant permissivement.

Au-delà du schéma Pydantic, quelques contrôles DÉTERMINISTES (en Python, jamais
laissés au seul prompt) refusent ce qui contredit l'entrée : indicateur cité
inexistant, donnée manquante masquée, tendance non démontrée, agent absent.
Ils ne vérifient PAS les chiffres écrits dans les textes libres — c'est le rôle
de la couche anti-hallucination dédiée.
"""
from __future__ import annotations

from typing import Optional

from pydantic import ValidationError

from app.ares.agents import dump_json, meta_depuis
from app.aura.schemas import (
    DonneeManquante,
    IndicatorAvailabilityInput,
    IndicatorAvailabilityOutput,
    IndicatorAvailabilityResponse,
    Mesure,
    NarrativeInput,
    NarrativeOutput,
    NarrativeResponse,
    PerformanceInput,
    PerformanceOutput,
    PerformanceResponse,
)
from app.aura.validation import IndicateurRef, nombres_dans_les_textes, verifier_texte
from app.gateway.cache import response_cache
from app.gateway.provider import ReponseLLMInvalide
from app.gateway.router import Gateway
from app.prompts.aura import AVAILABILITY_SYSTEM, NARRATIVE_SYSTEM, PERFORMANCE_SYSTEM

# Une narration ou un bulletin complets dépassent le défaut de 1024 tokens ; un
# JSON tronqué serait rejeté en 502.
_MAX_TOKENS_REDACTION = 2500
_MAX_TOKENS_DISPONIBILITE = 400


class DonneesInsuffisantes(ValueError):
    """Rien à raconter : aucun appel LLM n'est fait (ni coût, ni texte inventé)."""


# ----- Entrée du LLM : un statut explicite, jamais un null ambigu -----
def _mesure(valeur: Mesure) -> int | float | dict:
    """Nombre tel quel (0 compris) ; donnée manquante en objet de statut explicite."""
    if isinstance(valeur, DonneeManquante):
        return {"statut": "donnee_manquante", "motif": valeur.motif}
    return valeur


def _payload_narration(req: NarrativeInput) -> dict:
    indicateurs = []
    for ind in req.indicateurs_calcules:
        item: dict = {
            "id": ind.id,
            "libelle": ind.libelle,
            "statut": "calcule",
            "valeur": ind.valeur,
            "unite": ind.unite,
        }
        if ind.valeur_precedente is not None:
            item["valeur_precedente"] = _mesure(ind.valeur_precedente)
        indicateurs.append(item)
    return {
        "objectif_principal": req.objectif_principal or None,
        "locale": req.locale,
        "periode": req.periode.model_dump(mode="json") if req.periode else None,
        "indicateurs_calcules": indicateurs,
        "donnees_manquantes": [
            {"id": d.id, "libelle": d.libelle, "statut": "donnee_manquante", "motif": d.motif}
            for d in req.donnees_manquantes
        ],
        "indicateurs_en_erreur": [
            {"id": e.id, "libelle": e.libelle, "statut": "erreur", "motif": e.motif}
            for e in req.indicateurs_en_erreur
        ],
    }


def _payload_performance(req: PerformanceInput) -> dict:
    payload: dict = {
        "periode": req.periode.model_dump(mode="json"),
        "locale": req.locale,
    }
    for agent in ("ares", "apex"):
        agregats = getattr(req, agent)
        if agregats is not None:
            payload[agent] = {cle: _mesure(v) for cle, v in agregats.items()}
    return payload


def _payload_disponibilite(req: IndicatorAvailabilityInput) -> dict:
    return {
        "locale": req.locale,
        "indicateur_id": req.indicateur_id,
        "libelle": req.libelle,
        "sources_requises": [s.model_dump(mode="json") for s in req.sources_requises],
    }


# ----- Appel + validation stricte -----
async def _appeler(gw: Gateway, tier: str, system: str, user: str, workspace_id, max_tokens: int):
    """Appel via le Gateway ; rend (données JSON, info, nettoyeur de cache)."""
    data, info = await gw.complete_json(tier, system, user, workspace_id, max_tokens=max_tokens)

    def ecarter_du_cache() -> None:
        # Le Gateway met en cache une réponse AVANT toute validation métier.
        # Si elle est jugée invalide, la garder la resservirait telle quelle à
        # chaque nouvelle tentative du backend : on l'écarte.
        cle = response_cache.make_key(workspace_id, tier, system, user, max_tokens)
        response_cache.delete(cle)

    return data, info, ecarter_du_cache


def _valider(modele, data: dict, ecarter_du_cache):
    try:
        return modele.model_validate(data)
    except ValidationError as exc:
        ecarter_du_cache()
        raise ReponseLLMInvalide(
            f"réponse non conforme au contrat {modele.__name__} : "
            f"{exc.error_count()} erreur(s) — {exc.errors()[0]['loc']} {exc.errors()[0]['msg']}"
        ) from exc


def _refuser(ecarter_du_cache, message: str):
    ecarter_du_cache()
    raise ReponseLLMInvalide(message)


# ----- Chiffres fournis : seuls autorisés dans les textes libres -----
def _composantes_periode(periode) -> list[float]:
    if periode is None:
        return []
    return [float(x) for d in (periode.debut, periode.fin) for x in (d.year, d.month, d.day)]


def _numerique(m: Mesure | None) -> Optional[float]:
    return None if m is None or isinstance(m, DonneeManquante) else float(m)


def _refs_narration(req: NarrativeInput) -> list[IndicateurRef]:
    """Ce que le backend a fourni, indicateur par indicateur (statut, valeur, unité)."""
    refs = [
        IndicateurRef(i.id, i.libelle, "calcule", float(i.valeur),
                      tuple(v for v in (_numerique(i.valeur_precedente),) if v is not None), i.unite)
        for i in req.indicateurs_calcules
    ]
    refs += [IndicateurRef(d.id, d.libelle, "manquant") for d in req.donnees_manquantes]
    refs += [IndicateurRef(e.id, e.libelle, "erreur") for e in req.indicateurs_en_erreur]
    return refs


def _refs_agent(agent: str, agregats) -> list[IndicateurRef]:
    if agregats is None:
        return []
    return [
        IndicateurRef(f"{agent}.{cle}", cle.replace("_", " "),
                      "manquant" if isinstance(v, DonneeManquante) else "calcule",
                      _numerique(v))
        for cle, v in agregats.items()
    ]


def _autorises_narration(req: NarrativeInput) -> list[float]:
    """Chiffres fournis hors indicateurs : période, décomptes, textes du backend."""
    decomptes = [  # « 3 indicateurs », « 1 donnée manquante »
        len(req.indicateurs_calcules), len(req.donnees_manquantes), len(req.indicateurs_en_erreur),
        len(req.indicateurs_calcules) + len(req.donnees_manquantes) + len(req.indicateurs_en_erreur),
    ]
    return [
        *_composantes_periode(req.periode),
        *(float(n) for n in decomptes if n > 0),  # un décompte nul n'autorise JAMAIS « 0 »
        *nombres_dans_les_textes([req.objectif_principal]),
    ]


def _autorises_performance(req: PerformanceInput) -> list[float]:
    return [
        *_composantes_periode(req.periode),
        *(float(len(a)) for a in (req.ares, req.apex) if a is not None),
    ]


def _verifier_chiffres(controles, ecarter_du_cache) -> None:
    """Refuse la réponse si un texte contredit les données du backend.

    `controles` : (texte, indicateurs, indicateur_par_defaut, chiffres_autorises).
    """
    problemes = [p for texte, refs, defaut, autorises in controles
                 for p in verifier_texte(texte, refs, defaut=defaut, autorises=autorises)]
    if problemes:
        _refuser(ecarter_du_cache,
                 "chiffre(s) incohérent(s) avec les données fournies : " + " | ".join(problemes[:3]))


# ----- 1. Narration -----
def _tendance_demontrable(valeur, precedente: Mesure | None) -> set[str]:
    """Tendances que les données permettent d'affirmer pour cet indicateur."""
    if precedente is None or isinstance(precedente, DonneeManquante):
        return {"indeterminee"}
    if valeur > precedente:
        return {"hausse", "indeterminee", "stable"}
    if valeur < precedente:
        return {"baisse", "indeterminee", "stable"}
    return {"stable", "indeterminee"}


async def generate_narrative(gw: Gateway, req: NarrativeInput) -> NarrativeResponse:
    if not (req.indicateurs_calcules or req.donnees_manquantes or req.indicateurs_en_erreur):
        raise DonneesInsuffisantes("aucun indicateur fourni : rien à raconter.")

    data, info, ecarter = await _appeler(
        gw, "reasoning", NARRATIVE_SYSTEM, dump_json(_payload_narration(req)),
        req.workspace_id, _MAX_TOKENS_REDACTION,
    )
    sortie = _valider(NarrativeOutput, data, ecarter)

    calcules = {i.id: i for i in req.indicateurs_calcules}
    for analyse in sortie.analyses:
        ind = calcules.get(analyse.indicateur_id)
        if ind is None:
            _refuser(ecarter, f"analyse d'un indicateur absent des indicateurs calculés : "
                              f"« {analyse.indicateur_id} ».")
        if analyse.tendance not in _tendance_demontrable(ind.valeur, ind.valeur_precedente):
            _refuser(ecarter, f"tendance « {analyse.tendance} » non démontrée par les données "
                              f"pour « {ind.id} ».")

    # Aucune donnée manquante masquée, ajoutée ou réécrite.
    if {d.id for d in sortie.donnees_manquantes} != {d.id for d in req.donnees_manquantes} or (
        len(sortie.donnees_manquantes) != len(req.donnees_manquantes)
    ):
        _refuser(ecarter, "les données manquantes de la réponse ne correspondent pas à l'entrée.")

    # Idem pour les erreurs de calcul : jamais converties, masquées ni inventées.
    if {e.id for e in sortie.indicateurs_en_erreur} != {e.id for e in req.indicateurs_en_erreur} or (
        len(sortie.indicateurs_en_erreur) != len(req.indicateurs_en_erreur)
    ):
        _refuser(ecarter, "les indicateurs en erreur de la réponse ne correspondent pas à l'entrée.")

    # Aucun chiffre inventé, estimé, calculé ni rattaché au mauvais indicateur.
    refs, autorises = _refs_narration(req), _autorises_narration(req)
    _verifier_chiffres(
        [(sortie.resume, refs, None, autorises),
         *((a.commentaire, refs, a.indicateur_id, autorises) for a in sortie.analyses),
         *((p, refs, None, autorises) for p in sortie.points_attention)],
        ecarter,
    )

    # Ce qui est rendu au backend est la vérité de l'entrée, pas la recopie du modèle.
    sortie.donnees_manquantes = list(req.donnees_manquantes)
    sortie.indicateurs_en_erreur = list(req.indicateurs_en_erreur)
    return NarrativeResponse(**sortie.model_dump(), meta=meta_depuis(info))


# ----- 2. Bulletin ARES / APEX -----
async def generate_performance_bulletin(gw: Gateway, req: PerformanceInput) -> PerformanceResponse:
    data, info, ecarter = await _appeler(
        gw, "reasoning", PERFORMANCE_SYSTEM, dump_json(_payload_performance(req)),
        req.workspace_id, _MAX_TOKENS_REDACTION,
    )
    sortie = _valider(PerformanceOutput, data, ecarter)

    for agent in ("ares", "apex"):
        agregats = getattr(req, agent)
        bulletin = getattr(sortie, agent)
        if agregats is None and bulletin is not None:
            _refuser(ecarter, f"bulletin « {agent} » produit sans agrégats {agent} en entrée.")
        if agregats is not None and bulletin is None:
            _refuser(ecarter, f"bulletin « {agent} » absent alors que ses agrégats sont fournis.")
        if bulletin is not None:
            inconnues = [c for c in bulletin.metriques_citees if c not in agregats]
            if inconnues:
                _refuser(ecarter, f"métriques citées absentes des agrégats {agent} : {inconnues}.")

    refs_ares, refs_apex = _refs_agent("ares", req.ares), _refs_agent("apex", req.apex)
    autorises = _autorises_performance(req)
    controles = [(t, refs_ares + refs_apex, None, autorises)
                 for t in (sortie.resume, *sortie.recommandations)]
    for bulletin, refs in ((sortie.ares, refs_ares), (sortie.apex, refs_apex)):
        if bulletin is not None:  # un texte d'agent ne cite que les agrégats de cet agent
            controles += [(t, refs, None, autorises)
                          for t in (bulletin.synthese, *bulletin.points_forts, *bulletin.points_vigilance)]
    _verifier_chiffres(controles, ecarter)

    return PerformanceResponse(**sortie.model_dump(), meta=meta_depuis(info))


# ----- 3. Disponibilité d'un indicateur -----
async def check_indicator_availability(
    gw: Gateway, req: IndicatorAvailabilityInput
) -> IndicatorAvailabilityResponse:
    data, info, ecarter = await _appeler(
        gw, "fast", AVAILABILITY_SYSTEM, dump_json(_payload_disponibilite(req)),
        req.workspace_id, _MAX_TOKENS_DISPONIBILITE,
    )
    sortie = _valider(IndicatorAvailabilityOutput, data, ecarter)

    # Invariant vérifiable sans LLM : une source non connectée ne peut pas
    # alimenter un indicateur « disponible ».
    if sortie.disponible and any(not s.connectee for s in req.sources_requises):
        _refuser(ecarter, "indicateur déclaré disponible alors qu'une source n'est pas connectée.")

    return IndicatorAvailabilityResponse(**sortie.model_dump(), meta=meta_depuis(info))
