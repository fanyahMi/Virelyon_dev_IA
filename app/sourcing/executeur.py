"""Exécution d'un plan de recherche : requêtes → leads bruts.

Enchaîne quatre étapes :
1. construire le plan (`builder/plan_recherche.py`) ;
2. **découverte** — interroger les sources qui trouvent des entreprises inconnues ;
3. écarter les leads dont le secteur est explicitement exclu par l'ICP ;
4. **enrichissement** — compléter les leads retenus (email du décideur).

L'ordre 3 avant 4 n'est pas cosmétique : enrichir un lead qu'on s'apprête à jeter
consommerait des crédits Hunter pour rien.

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
from app.sourcing.apify import ApifyLinkedIn
from app.sourcing.apollo import Apollo
from app.sourcing.base import Connecteur, domaine_de
from app.sourcing.hunter import Hunter, HunterDiscover
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
    tous = (
        Apollo(s.apollo_api_key),
        Places(s.google_places_api_key),
        # Une seule clé Hunter alimente ses deux endpoints (découverte + email).
        HunterDiscover(s.hunter_api_key),
        Hunter(s.hunter_api_key),
        SiteWeb(),
        # Apify est l'exécutant, pas la source : la source reste « linkedin ».
        ApifyLinkedIn(s.apify_api_token, s.apify_chercher_emails),
    )
    return {c.source: c for c in tous}


def _erreur(exc: httpx.HTTPError) -> str:
    """Message d'échec réseau, lisible par l'appelant."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code} — {exc.response.reason_phrase}"
    return f"{exc.__class__.__name__} : {exc}"


def _statut_sans_appel(
    connecteur: Connecteur, bloc: BlocRecherche, requetes: list, dry_run: bool
) -> ResultatSource | None:
    """Statut décidable sans réseau : non branché, simulation, clé absente.

    Rend `None` quand l'appel réel peut avoir lieu. Partagé par les deux natures :
    sans ça, découverte et enrichissement finiraient par diverger sur des cas
    strictement identiques.
    """
    if connecteur.motif_non_implemente:
        # La simulation couvre AUSSI les sources non branchées : leur plan est
        # déjà construit, autant le montrer — c'est tout l'intérêt du mode.
        return ResultatSource(
            source=bloc.source,
            statut="non_implemente",
            requetes=requetes if dry_run else [],
            erreur=connecteur.motif_non_implemente,
        )
    if dry_run:
        return ResultatSource(source=bloc.source, statut="simule", requetes=requetes)
    if not connecteur.configure():
        return ResultatSource(
            source=bloc.source,
            statut="non_configuree",
            requetes=requetes,
            erreur=f"{connecteur.variable_cle} non renseignée.",
        )
    return None


async def _decouvrir(
    connecteur: Connecteur, bloc: BlocRecherche, limite: int, dry_run: bool
) -> tuple[ResultatSource, list[Lead]]:
    """Un connecteur de découverte, un bloc. N'échoue jamais : rend toujours un
    `ResultatSource`."""
    requetes = connecteur.apercu(bloc, limite)
    prealable = _statut_sans_appel(connecteur, bloc, requetes, dry_run)
    if prealable is not None:
        return prealable, []

    try:
        leads = await connecteur.executer(bloc, limite)
    except httpx.HTTPError as exc:
        return ResultatSource(
            source=bloc.source, statut="erreur", requetes=requetes, erreur=_erreur(exc)
        ), []

    return ResultatSource(
        source=bloc.source, statut="ok", requetes=requetes, nb_leads=len(leads)
    ), leads


async def _enrichir(
    connecteur: Connecteur, bloc: BlocRecherche, leads: list[Lead], limite: int, dry_run: bool
) -> ResultatSource:
    """Un connecteur d'enrichissement, sur les leads déjà découverts.

    Ces trois cas sont traités avant tout calcul de requête : sans découverte
    préalable, `apercu_enrichissement()` n'a aucun domaine à décrire.
    """
    if connecteur.motif_non_implemente or dry_run or not connecteur.configure():
        return _statut_sans_appel(
            connecteur, bloc, connecteur.apercu(bloc, limite), dry_run
        )

    domaines = [d for d in (domaine_de(lead) for lead in leads) if d]
    requetes = connecteur.apercu_enrichissement(bloc, limite, domaines)
    try:
        nb = await connecteur.enrichir(leads, bloc, limite)
    except httpx.HTTPError as exc:
        return ResultatSource(
            source=bloc.source, statut="erreur", requetes=requetes, erreur=_erreur(exc)
        )

    return ResultatSource(
        source=bloc.source, statut="ok", requetes=requetes, nb_enrichis=nb
    )


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

    def couples(blocs: list[BlocRecherche]) -> list[tuple[Connecteur, BlocRecherche]]:
        return [(connecteurs[b.source], b) for b in blocs if b.source in connecteurs]

    # --- 1. Découverte -------------------------------------------------------
    # Les sources sont indépendantes : les enchaîner en séquence ferait payer la
    # somme des latences réseau au lieu de la plus longue.
    resultats = await asyncio.gather(
        *(_decouvrir(c, b, req.limite, req.dry_run) for c, b in couples(plan.decouverte))
    )
    par_source = [r for r, _ in resultats]
    leads = [lead for _, trouves in resultats for lead in trouves]

    # --- 2. Filtrage ICP, AVANT d'enrichir -----------------------------------
    # Aucune source externe ne sait exclure un secteur : on filtre ici, en
    # consommant la liste déjà canonisée par le plan. Le faire maintenant évite
    # de dépenser des crédits d'enrichissement sur des leads déjà écartés.
    exclus = set(plan.secteurs_exclus)
    retenus = [lead for lead in leads if lead.secteur not in exclus]

    # --- 3. Enrichissement ---------------------------------------------------
    # En séquence, contrairement à la découverte : les enrichisseurs modifient les
    # MÊMES objets `Lead`. Les paralléliser exposerait un lead à deux écritures
    # concurrentes, et le résultat dépendrait de l'ordre d'arrivée du réseau.
    for connecteur, bloc in couples(plan.enrichissement):
        par_source.append(
            await _enrichir(connecteur, bloc, retenus, req.limite, req.dry_run)
        )

    if req.dry_run:
        diagnostics.append(_DIAG_SIMULATION)

    return ExecuterPlanResponse(
        leads=retenus,
        par_source=par_source,
        rejetes_hors_icp=len(leads) - len(retenus),
        diagnostics=diagnostics,
    )
