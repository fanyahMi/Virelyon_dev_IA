"""ICP structuré → plan de recherche exploitable par les sources externes.

C'est le chaînon entre l'Agent Builder et le sourcing : le client définit *qui*
il veut cibler, ce module traduit en *quoi chercher où*.

**Logique PURE — aucun appel LLM.** Choix assumé : une même configuration doit
toujours produire les mêmes requêtes. Un plan de recherche qui varie d'une
exécution à l'autre rendrait le sourcing impossible à déboguer et la facturation
au volume imprévisible. La traduction passe donc par les tables versionnées de
`referentiels.py`, pas par un modèle.

Deux natures de source, à ne pas confondre :
- **découverte** — trouve des entreprises inconnues (Maps, Apollo, Hunter Discover) ;
- **enrichissement** — complète une entreprise déjà trouvée (site web, Hunter).
"""
from __future__ import annotations

from app.builder.referentiels import (
    LIBELLES_RECHERCHE,
    SENIORITE_LINKEDIN_PAR_ROLE,
    SENIORITE_PAR_ROLE,
    TITRES_PAR_ROLE,
    TRANCHES_LINKEDIN,
    canoniser_secteur,
    code_pays,
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

# LinkedIn n'expose toujours aucune API de recherche : la collecte passe par un
# acteur Apify qui scrape la plateforme. Le connecteur existe désormais, mais la
# réserve juridique n'a pas disparu — elle a changé de main. On l'expose donc au
# client dans la réponse plutôt que de la laisser en commentaire.
_AVERTISSEMENT_LINKEDIN = (
    "Collecte assurée par un acteur Apify qui scrape LinkedIn : la plateforme n'expose "
    "aucune API de recherche et cette pratique contrevient à ses conditions "
    "d'utilisation. Les profils sont des données personnelles (RGPD) — s'assurer d'une "
    "base légale et d'une politique de conservation avant tout usage en production. "
    "Apollo et Hunter donnent accès aux mêmes décideurs par des voies contractuelles."
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
    "Désactiver cette source si l'usage doit rester strictement contractuel.",
)
_DIAG_EXCLUSIONS = diag(
    "avertissement",
    "icp.secteurs_exclus",
    "Les secteurs exclus ne peuvent pas être filtrés à la source : ils seront "
    "écartés après collecte, par la qualification.",
)
_DIAG_ZONE_HUNTER = diag(
    "avertissement",
    "zone",
    "Hunter Discover ne géolocalise pas du texte libre : il exige un pays. La zone "
    "fournie n'a pas pu être rattachée à un pays, la recherche sera donc mondiale.",
    "Indiquer le pays (« France ») ou « Ville, Pays » (« Lyon, France »). "
    "Un code ISO à deux lettres est également accepté.",
)
_DIAG_SECTEUR_LINKEDIN = diag(
    "avertissement",
    "sources",
    "La recherche LinkedIn est floue (mots-clés), pas taxonomique : une requête "
    "« marketing » ramène aussi des profils sans rapport. Le secteur des leads n'est "
    "donc PAS renseigné — c'est la qualification qui tranchera.",
    "Compter sur /ares/qualify plutôt que sur le filtrage par secteur pour cette source.",
)
_DIAG_VILLE_HUNTER = diag(
    "info",
    "zone",
    "Le filtrage par ville dépend de l'orthographe qu'emploie Hunter, souvent "
    "anglaise : une ville non reconnue ne remonte aucun résultat, sans erreur.",
    "En cas de résultat vide, ne garder que le pays dans la zone.",
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


# Tranches d'effectif de Hunter Discover — liste fermée, l'API n'accepte rien d'autre.
# ⚠️ Une valeur hors liste n'est pas rejetée : elle est **silencieusement ignorée**
# (le filtre disparaît de `meta.filters` sans erreur). D'où une table figée ici
# plutôt qu'une chaîne construite à la volée.
_TRANCHES_HUNTER: tuple[tuple[str, int, int], ...] = (
    ("1-10", 1, 10),
    ("11-50", 11, 50),
    ("51-200", 51, 200),
    ("201-500", 201, 500),
    ("501-1000", 501, 1000),
    ("1001-5000", 1001, 5000),
    ("5001-10000", 5001, 10000),
    ("10001+", 10001, 10**9),
)


def _tranches_hunter(icp: ICP) -> list[str]:
    """Toutes les tranches Hunter que chevauche la fourchette de l'ICP.

    Hunter accepte une **liste** de tranches : un ICP « 5 à 30 » se traduit donc
    par `["1-10", "11-50"]`, sans rien perdre. Ne garder qu'une tranche écarterait
    à la source des entreprises parfaitement dans la cible ; le tri fin sur les
    bornes exactes reste fait par le scoring, qui les connaît.
    """
    if icp.taille_min is None and icp.taille_max is None:
        return []
    lo = icp.taille_min if icp.taille_min is not None else 1
    hi = icp.taille_max if icp.taille_max is not None else 10**9
    return [nom for nom, bas, haut in _TRANCHES_HUNTER if bas <= hi and lo <= haut]


def _tranches_linkedin(icp: ICP) -> list[str]:
    """Codes de tranche LinkedIn que chevauche la fourchette de l'ICP.

    Même principe que pour Hunter : LinkedIn accepte une liste, on ne perd donc
    rien à couvrir toutes les tranches touchées ; le tri fin sur les bornes
    exactes reste au scoring, qui les connaît. `A` (Self-Employed) n'est retenu
    que si l'ICP descend jusqu'à l'indépendant.
    """
    if icp.taille_min is None and icp.taille_max is None:
        return []
    lo = icp.taille_min if icp.taille_min is not None else 1
    hi = icp.taille_max if icp.taille_max is not None else 10**9
    return [code for code, bas, haut in TRANCHES_LINKEDIN if bas <= hi and lo <= haut]


def _seniorites_linkedin(icp: ICP) -> list[str]:
    """Identifiants de niveau hiérarchique LinkedIn déduits des rôles ciblés."""
    sortie: list[str] = []
    for role in icp.roles_cibles:
        canon = normaliser_role(role)
        for niveau in SENIORITE_LINKEDIN_PAR_ROLE.get(canon, ()) if canon else ():
            if niveau not in sortie:
                sortie.append(niveau)
    return sortie


def _requetes_linkedin(icp: ICP) -> dict[str, str]:
    """Secteur canonique → terme de recherche floue LinkedIn.

    Une entrée par secteur, et donc une exécution par secteur : l'acteur ne prend
    qu'un `searchQuery` à la fois. Un secteur personnalisé retombe sur la saisie
    du client, qui reste un mot-clé valable.
    """
    par_secteur: dict[str, str] = {}
    for secteur in icp.secteurs_inclus:
        canon = normaliser_secteur(secteur)
        termes = LIBELLES_RECHERCHE.get(canon, {}).get("linkedin", ()) if canon else ()
        par_secteur[canon or canoniser_secteur(secteur)] = (
            termes[0] if termes else secteur.strip()
        )
    return par_secteur


def _industries_hunter(icp: ICP) -> tuple[dict[str, list[str]], list[str]]:
    """(secteur canonique → industries Hunter, secteurs sans équivalent).

    Une entrée par secteur, et non une liste à plat : la réponse de Discover ne
    rappelle **pas** l'industrie trouvée (elle ne rend que `domain`, `organization`
    et `emails_count`). Interroger un secteur à la fois est donc le seul moyen
    d'attribuer un secteur au lead sans le deviner.
    """
    par_secteur: dict[str, list[str]] = {}
    sans_equivalent: list[str] = []
    for secteur in icp.secteurs_inclus:
        canon = normaliser_secteur(secteur)
        valeurs = LIBELLES_RECHERCHE.get(canon, {}).get("hunter", ()) if canon else ()
        if valeurs:
            par_secteur[canon] = list(valeurs)
        else:
            sans_equivalent.append(secteur)
    return par_secteur, sans_equivalent


def _localisation(zone: str | None) -> tuple[list[dict], bool]:
    """(filtre de localisation Hunter, la zone a-t-elle été comprise).

    Format imposé par l'API : `{"country": "FR"}`, la ville n'étant acceptée
    qu'accompagnée de son pays. La zone vient toujours du client, jamais d'une
    déduction (CDCF §8) — on se contente d'en changer le format.
    """
    if not zone:
        return [], True
    morceaux = [m.strip() for m in zone.split(",") if m.strip()]
    pays = code_pays(morceaux[-1]) if morceaux else None
    if pays is None:
        return [], False
    lieu = {"country": pays}
    if len(morceaux) > 1:
        lieu["city"] = morceaux[0]
    return [lieu], True


def _seniorites(icp: ICP) -> list[str]:
    """Niveaux hiérarchiques Hunter déduits des rôles ciblés."""
    sortie: list[str] = []
    for role in icp.roles_cibles:
        canon = normaliser_role(role)
        niveau = SENIORITE_PAR_ROLE.get(canon) if canon else None
        if niveau and niveau not in sortie:
            sortie.append(niveau)
    return sortie


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

    # --- LinkedIn (via Apify) : filtres de recherche de personnes -----------
    # Les clés portent les noms attendus par l'acteur : le connecteur les recopie
    # sans les traduire. Une clé renommée ici se verrait immédiatement en dry_run,
    # là où une traduction intermédiaire masquerait l'erreur.
    if "linkedin" in demandees:
        decouverte.append(
            BlocRecherche(
                source="linkedin",
                type="filtres",
                filtres=_filtres(
                    requetes_par_secteur=_requetes_linkedin(icp),
                    currentJobTitles=titres,
                    companyHeadcount=_tranches_linkedin(icp),
                    seniorityLevelIds=_seniorites_linkedin(icp),
                    # LinkedIn comprend mal les abréviations de pays (« UK » →
                    # Ukraine) : on transmet la zone telle que le client l'a écrite.
                    locations=[req.zone] if req.zone else None,
                ),
                avertissement=_AVERTISSEMENT_LINKEDIN,
            )
        )
        diags.append(_DIAG_LINKEDIN)
        if icp.secteurs_inclus:
            diags.append(_DIAG_SECTEUR_LINKEDIN)

    # --- Hunter Discover : découverte par critères structurés ---------------
    if "hunter_discover" in demandees:
        industries, sans_industrie = _industries_hunter(icp)
        localisation, zone_comprise = _localisation(req.zone)
        decouverte.append(
            BlocRecherche(
                source="hunter_discover",
                type="filtres",
                filtres=_filtres(
                    industries_par_secteur=industries,
                    headcount=_tranches_hunter(icp),
                    headquarters_location=localisation,
                ),
            )
        )
        if not zone_comprise:
            diags.append(_DIAG_ZONE_HUNTER)
        elif any("city" in lieu for lieu in localisation):
            diags.append(_DIAG_VILLE_HUNTER)
        for secteur in sans_industrie:
            diags.append(
                diag(
                    "avertissement",
                    "icp.secteurs_inclus",
                    f"« {secteur} » n'a pas d'équivalent dans la nomenclature fermée "
                    f"de Hunter : ce secteur ne sera pas cherché sur cette source.",
                    "Le chercher via Google Maps ou Apollo, qui acceptent du texte libre.",
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

    # --- Hunter : email du décideur sur un domaine connu --------------------
    if "hunter" in demandees:
        enrichissement.append(
            BlocRecherche(
                source="hunter",
                type="domain_search",
                filtres=_filtres(
                    titres_recherches=titres,
                    # Hunter ne filtre pas sur des intitulés libres mais sur trois
                    # niveaux fermés : sans eux, il rendrait aussi bien le PDG que
                    # l'adresse du support.
                    seniority=_seniorites(icp),
                ),
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
