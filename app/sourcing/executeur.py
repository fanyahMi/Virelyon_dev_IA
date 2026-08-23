"""Exécution d'un plan de recherche : requêtes → leads bruts.

Enchaîne trois étapes :
1. construire le plan (`builder/plan_recherche.py`) ;
2. exécuter chaque source via son connecteur — ou la simuler en `dry_run` ;
3. écarter les leads dont le secteur est explicitement exclu par l'ICP.

Ce qu'on ne fait PAS ici : qualifier, scorer, persister. Le sourcing ramène de la
matière ; le jugement reste aux endpoints ARES.
"""
from __future__ import annotations

import asyncio

import httpx

from app.builder.plan_recherche import construire_plan
from app.core.config import get_settings
from app.schemas.ares import Lead
from app.schemas.builder import BlocRecherche, Diagnostic, PlanRechercheRequest, diag
from app.schemas.sourcing import (
    ExecuterPlanRequest,
    ExecuterPlanResponse,
    ResultatSource,
)
from app.sourcing.apollo import Apollo
from app.sourcing.base import Connecteur
from app.sourcing.hunter import Hunter
from app.sourcing.linkedin import LinkedIn
from app.sourcing.openstreetmap import OpenStreetMap
from app.sourcing.places import Places
from app.sourcing.site_web import SiteWeb

_DIAG_SIMULATION = diag(
    "info",
    "dry_run",
    "Mode simulation : aucune requête n'a été envoyée. Les appels qui seraient "
    "effectués figurent dans `par_source[].requetes`.",
    "Passer dry_run à false une fois les clés d'API configurées.",
)


def _connecteurs() -> dict[str, Connecteur]:
    """Registre UNIQUE des sources. Une source non branchée y figure aussi, avec
    son motif — c'est ce qui évite qu'elle disparaisse silencieusement."""
    s = get_settings()
    tous = (Apollo(s.apollo_api_key), Places(s.google_places_api_key),
            OpenStreetMap(), Hunter(s.hunter_api_key), SiteWeb(), LinkedIn())
    return {c.source: c for c in tous}


async def _executer_bloc(
    connecteur: Connecteur, bloc: BlocRecherche, limite: int, dry_run: bool
) -> tuple[ResultatSource, list[Lead]]:
    """Un connecteur, un bloc. N'échoue jamais : rend toujours un `ResultatSource`."""
    if connecteur.motif_non_implemente and not dry_run:
        return (
            ResultatSource(
                source=bloc.source,
                statut="non_implemente",
                erreur=connecteur.motif_non_implemente,
            ),
            [],
        )

    requetes = connecteur.apercu(bloc, limite)

    def resultat(statut: str, **extra) -> ResultatSource:
        return ResultatSource(
            source=bloc.source, statut=statut, requetes=requetes, **extra
        )

    if dry_run:
        # La simulation couvre AUSSI les sources non branchées : leur plan est
        # déjà construit, autant le montrer — c'est tout l'intérêt du mode.
        if connecteur.motif_non_implemente:
            return resultat("non_implemente", erreur=connecteur.motif_non_implemente), []
        return resultat("simule"), []

    if not connecteur.configure():
        return resultat(
            "non_configuree", erreur=f"{connecteur.variable_cle} non renseignée."
        ), []

    try:
        leads = await connecteur.executer(bloc, limite)
    except httpx.HTTPStatusError as exc:
        return resultat(
            "erreur",
            erreur=f"HTTP {exc.response.status_code} — {exc.response.reason_phrase}",
        ), []
    except httpx.HTTPError as exc:
        return resultat("erreur", erreur=f"{exc.__class__.__name__} : {exc}"), []

    return resultat("ok", nb_leads=len(leads)), leads


async def executer_plan(req: ExecuterPlanRequest) -> ExecuterPlanResponse:
    plan = construire_plan(
        PlanRechercheRequest(
            workspace_id=req.workspace_id,
            icp=req.icp,
            sources=req.sources,
            zone=req.zone,
        )
    )
    diagnostics: list[Diagnostic] = list(plan.diagnostics)

    # Une erreur bloquante du plan (ex. aucun secteur ciblé) arrête tout :
    # exécuter des requêtes vides coûterait de l'argent pour rien.
    if any(d.niveau == "erreur" for d in diagnostics):
        return ExecuterPlanResponse(diagnostics=diagnostics)

    connecteurs = _connecteurs()
    couples = [
        (connecteurs[bloc.source], bloc)
        for bloc in plan.decouverte + plan.enrichissement
        if bloc.source in connecteurs
    ]

    # Les sources sont indépendantes : les enchaîner en séquence ferait payer la
    # somme des latences réseau au lieu de la plus longue.
    resultats = await asyncio.gather(
        *(_executer_bloc(c, b, req.limite, req.dry_run) for c, b in couples)
    )
    par_source = [r for r, _ in resultats]
    leads = [lead for _, trouves in resultats for lead in trouves]

    # Aucune source externe ne sait exclure un secteur : on filtre ici, en
    # consommant la liste déjà canonisée par le plan.
    exclus = set(plan.secteurs_exclus)
    retenus = [lead for lead in leads if lead.secteur not in exclus]

    if req.dry_run:
        diagnostics.append(_DIAG_SIMULATION)

    return ExecuterPlanResponse(
        leads=retenus,
        par_source=par_source,
        rejetes_hors_icp=len(leads) - len(retenus),
        diagnostics=diagnostics,
    )
