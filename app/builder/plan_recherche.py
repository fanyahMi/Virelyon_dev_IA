"""ICP structuré → plan de recherche exploitable par les sources externes.

C'est le chaînon entre l'Agent Builder et le sourcing : le client définit *qui*
il veut cibler, ce module traduit en *quoi chercher où*.

**Logique PURE — aucun appel LLM.** Choix assumé : une même configuration doit
toujours produire les mêmes requêtes. Un plan de recherche qui varie d'une
exécution à l'autre rendrait le sourcing impossible à déboguer et la facturation
au volume imprévisible. La traduction passe donc par les tables versionnées de
`referentiels.py`, pas par un modèle.

Deux natures de source, à ne pas confondre :
- **découverte** — trouve des entreprises inconnues (Maps, Apollo, LinkedIn) ;
- **enrichissement** — complète une entreprise déjà trouvée (site web, Hunter).
"""
from __future__ import annotations

from app.builder.referentiels import (
    LIBELLES_RECHERCHE,
    TITRES_PAR_ROLE,
    canoniser_secteur,
    normaliser_role,
    normaliser_secteur,
)
from app.schemas.ares import ICP
from app.schemas.builder import (
    SOURCES_CONNUES,
    SOURCES_DECOUVERTE,
    SOURCES_ENRICHISSEMENT,
    BlocRecherche,
    Diagnostic,
    PlanRechercheRequest,
    PlanRechercheResponse,
    diag,
)

# LinkedIn n'expose aucune API de recherche : toute collecte y passe par de
# l'automatisation de navigateur, contraire à ses conditions d'utilisation.
_AVERTISSEMENT_LINKEDIN = (
    "LinkedIn n'expose pas d'API de recherche : ces filtres ne sont exploitables que "
    "par automatisation de navigateur, contraire à ses conditions d'utilisation. "
    "Les mêmes décideurs sont accessibles légalement via Apollo."
)

# Diagnostics au texte fixe — sortis du flux de contrôle pour que la logique par
# source tienne en quelques lignes lisibles.
_DIAG_SANS_SECTEUR = diag(
    "erreur",
    "icp.secteurs_inclus",
    "Aucun secteur ciblé : impossible de construire une requête de recherche. "
    "Le sourcing ne peut pas démarrer.",
    "Renseigner au moins un secteur dans l'ICP.",
)
_DIAG_SANS_ZONE = diag(
    "avertissement",
    "zone",
    "Aucune zone fournie : Google Places renverra des résultats dispersés et peu "
    "exploitables.",
    "Demander au client la zone à prospecter.",
)
_DIAG_LINKEDIN = diag(
    "avertissement", "sources", _AVERTISSEMENT_LINKEDIN,
    "Utiliser Apollo pour identifier les décideurs.",
)
_DIAG_EXCLUSIONS = diag(
    "avertissement",
    "icp.secteurs_exclus",
    "Les secteurs exclus ne peuvent pas être filtrés à la source : ils seront "
    "écartés après collecte, par la qualification.",
)


def _filtres(**paires) -> dict:
    """Ne retient que les critères réellement renseignés."""
    return {cle: valeur for cle, valeur in paires.items() if valeur}


def _libelles(icp: ICP, cle: str) -> tuple[list[str], list[str]]:
    """(termes de recherche, secteurs sans libellé prédéfini).

    Un secteur personnalisé n'a pas de libellés : on retombe sur le libellé saisi
    par le client, qui reste une requête de recherche valable. Le second élément
    permet à l'appelant de le signaler sans refaire le test.
    """
    sortie: list[str] = []
    sans_libelle: list[str] = []
    for secteur in icp.secteurs_inclus:
        canon = normaliser_secteur(secteur)
        termes = LIBELLES_RECHERCHE.get(canon, {}).get(cle, ()) if canon else ()
        if not termes:
            termes = (secteur.strip(),)  # repli : la saisie du client
            sans_libelle.append(secteur)
        for terme in termes:
            if terme and terme not in sortie:
                sortie.append(terme)
    return sortie, sans_libelle


def _titres(icp: ICP) -> list[str]:
    """Intitulés de poste à cibler, déduits des rôles de l'ICP."""
    sortie: list[str] = []
    for role in icp.roles_cibles:
        canon = normaliser_role(role)
        if canon is None:
            continue
        for titre in TITRES_PAR_ROLE.get(canon, ()):
            if titre not in sortie:
                sortie.append(titre)
    return sortie


def _tranche_effectif(icp: ICP) -> str | None:
    """Format attendu par Apollo : `"5,30"`. Bornes ouvertes si l'une manque."""
    if icp.taille_min is None and icp.taille_max is None:
        return None
    lo = icp.taille_min if icp.taille_min is not None else 1
    hi = icp.taille_max if icp.taille_max is not None else 10000
    return f"{lo},{hi}"


def construire_plan(req: PlanRechercheRequest) -> PlanRechercheResponse:
    icp = req.icp
    demandees = req.sources or list(SOURCES_CONNUES)
    diags: list[Diagnostic] = []

    for source in (s for s in demandees if s not in SOURCES_CONNUES):
        diags.append(
            diag(
                "avertissement",
                "sources",
                f"Source « {source} » inconnue : ignorée.",
                "Sources reconnues : " + ", ".join(SOURCES_CONNUES),
            )
        )

    if not icp.secteurs_inclus:
        diags.append(_DIAG_SANS_SECTEUR)

    titres = _titres(icp)
    tranche = _tranche_effectif(icp)
    decouverte: list[BlocRecherche] = []
    enrichissement: list[BlocRecherche] = []

    # Le repli « pas de libellé prédéfini » vaut pour toutes les sources de
    # découverte, pas seulement Maps : on le signale une fois, hors des blocs.
    _, sans_libelle = _libelles(icp, "maps")
    for secteur in sans_libelle:
        diags.append(
            diag(
                "info",
                "icp.secteurs_inclus",
                f"Aucun libellé de recherche prédéfini pour « {secteur} » : la saisie "
                f"du client est utilisée telle quelle comme requête.",
                "Ajouter des libellés dans LIBELLES_RECHERCHE si ce secteur revient souvent.",
            )
        )

    # --- Google Maps (Places) : requêtes texte ------------------------------
    if "google_maps" in demandees:
        libelles, _ = _libelles(icp, "maps")
        # La zone n'est JAMAIS déduite (CDCF §8) — concaténée si le client l'a fournie.
        requetes = [f"{lib} {req.zone}".strip() if req.zone else lib for lib in libelles]
        if libelles and not req.zone:
            diags.append(_DIAG_SANS_ZONE)
        decouverte.append(
            BlocRecherche(source="google_maps", type="requetes_texte", requetes=requetes)
        )

    # --- Apollo : filtres structurés ----------------------------------------
    if "apollo" in demandees:
        industries, _ = _libelles(icp, "apollo")
        decouverte.append(
            BlocRecherche(
                source="apollo",
                type="filtres",
                filtres=_filtres(
                    organization_industries=industries,
                    organization_num_employees_ranges=[tranche] if tranche else None,
                    person_titles=titres,
                ),
            )
        )

    # --- LinkedIn : filtres, mais réserve juridique -------------------------
    if "linkedin" in demandees:
        decouverte.append(
            BlocRecherche(
                source="linkedin",
                type="filtres",
                filtres=_filtres(
                    titres=titres,
                    taille_entreprise=tranche,
                    secteurs=[canoniser_secteur(x) for x in icp.secteurs_inclus],
                ),
                avertissement=_AVERTISSEMENT_LINKEDIN,
            )
        )
        diags.append(_DIAG_LINKEDIN)

    # --- OpenStreetMap : découverte locale, gratuite et sans clé ------------
    if "openstreetmap" in demandees:
        decouverte.append(
            BlocRecherche(
                source="openstreetmap",
                type="filtres",
                filtres=_filtres(
                    secteurs=[canoniser_secteur(x) for x in icp.secteurs_inclus],
                    zone=req.zone,
                ),
            )
        )
        if not req.zone:
            diags.append(
                diag(
                    "avertissement",
                    "zone",
                    "OpenStreetMap cherche dans une zone administrative : sans zone, "
                    "aucune requête n'est construite.",
                    "Renseigner la ville ou la région à prospecter.",
                )
            )

    # --- Site web : extraction (enrichissement) -----------------------------
    if "site_web" in demandees:
        enrichissement.append(
            BlocRecherche(
                source="site_web",
                type="extraction",
                champs_cibles=["secteur", "description", "taille_estimee", "signaux"],
            )
        )

    # --- Hunter : recherche d'email sur un domaine connu --------------------
    if "hunter" in demandees:
        enrichissement.append(
            BlocRecherche(
                source="hunter",
                type="domain_search",
                filtres=_filtres(titres_recherches=titres),
            )
        )

    # Aucune source externe ne sait exclure un secteur : le filtre est appliqué
    # après collecte, par la qualification.
    exclus = [canoniser_secteur(x) for x in icp.secteurs_exclus]
    if exclus:
        diags.append(_DIAG_EXCLUSIONS)

    return PlanRechercheResponse(
        decouverte=decouverte,
        enrichissement=enrichissement,
        secteurs_exclus=exclus,
        diagnostics=diags,
    )
