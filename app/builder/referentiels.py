"""Référentiels de l'Agent Builder — cohérence du vocabulaire, pas restriction.

Le filtrage (`ares/scoring.py:_fit`) fait une **égalité stricte** après passage en
minuscules : `"Marketing digital"` ne matche PAS `"marketing"`. Sans forme canonique
partagée, la correspondance tombe à zéro sur des leads parfaitement valides.

**Le catalogue ci-dessous est une liste de suggestions, PAS une liste fermée.**
Chaque client cible son propre marché : un client peut légitimement prospecter des
restaurants, des cliniques ou des industriels. `canoniser_secteur()` accepte donc
n'importe quelle valeur et se contente de la ramener à une forme stable.

> Ne pas confondre deux ICP : celui de **VIRELYON** (agences de services B2B, qui
> achètent la plateforme — contrainte de messaging CDCF §8) et celui de **chaque
> client** (qui il veut prospecter, et qui peut être n'importe quel marché).

Ce module est la **source unique** utilisée par :
- les listes déroulantes de l'écran Agent Builder (frontend, via le backend) ;
- la normalisation appliquée par Big Data sur `lead.secteur` ;
- l'extraction d'ICP depuis du texte libre (`builder/icp.py`) ;
- la construction du plan de recherche (`builder/plan_recherche.py`).

⚠️ Big Data **doit** appeler `canoniser_secteur()` à l'ingestion. C'est la seule
garantie que `lead.secteur` et `icp.secteurs_inclus` parlent le même langage.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

# --- Secteurs -----------------------------------------------------------------
# Catalogue de SUGGESTIONS proposé par défaut dans l'Agent Builder. Un client peut
# saisir un secteur absent de cette liste : il sera canonisé, pas rejeté.
SECTEURS_SERVICES_B2B: tuple[str, ...] = (
    "marketing",
    "communication",
    "conseil",
    "developpement",
    "design",
    "rh",
    "formation",
    "juridique",
    "comptabilite",
    "evenementiel",
    "traduction",
    "relation_presse",
)

# Autres secteurs courants, proposés eux aussi dans les listes.
# (Ceux-ci sont interdits en *témoignage marketing* — contrainte CDCF §8 — mais
# parfaitement légitimes comme cible de prospection d'un client.)
SECTEURS_AUTRES: tuple[str, ...] = (
    "hotellerie",
    "restauration",
    "retail",
    "sante",
    "artisanat",
    "immobilier",
    "industrie",
    "transport",
    "batiment",
    "education",
    "association",
)

SECTEURS: tuple[str, ...] = SECTEURS_SERVICES_B2B + SECTEURS_AUTRES

# --- Rôles de contact ---------------------------------------------------------
ROLES: tuple[str, ...] = (
    "fondateur",
    "decideur",
    "directeur",
    "manager",
    "operationnel",
)

# --- Tons de voix (CDCF §4.5 / présentation ARES) -----------------------------
TONS_DE_VOIX: tuple[str, ...] = (
    "professionnel",
    "chaleureux",
    "direct",
    "creatif",
)

# --- Canaux (CDCF §6) ---------------------------------------------------------
CANAUX: tuple[str, ...] = ("email", "whatsapp", "linkedin", "sms", "slack")


# --- Synonymes ----------------------------------------------------------------
# Formulations courantes rencontrées dans les sources de sourcing (Google Maps,
# LinkedIn, annuaires) → valeur canonique du référentiel.
_SYNONYMES: dict[str, str] = {
    # marketing
    "marketing digital": "marketing",
    "growth": "marketing",
    "growth marketing": "marketing",
    "webmarketing": "marketing",
    "agence marketing": "marketing",
    "publicite": "marketing",
    "seo": "marketing",
    # communication
    "com": "communication",
    "agence de com": "communication",
    "agence de communication": "communication",
    "relations publiques": "communication",
    "branding": "communication",
    # conseil
    "consulting": "conseil",
    "cabinet de conseil": "conseil",
    "strategie": "conseil",
    "audit": "conseil",
    # developpement
    "dev": "developpement",
    "developpement web": "developpement",
    "developpement logiciel": "developpement",
    "esn": "developpement",
    "agence web": "developpement",
    "informatique": "developpement",
    "logiciel": "developpement",
    # design
    "ux": "design",
    "ui": "design",
    "graphisme": "design",
    "studio de design": "design",
    "direction artistique": "design",
    # rh
    "ressources humaines": "rh",
    "recrutement": "rh",
    "cabinet de recrutement": "rh",
    "chasse de tetes": "rh",
    # formation
    "organisme de formation": "formation",
    "e-learning": "formation",
    "coaching": "formation",
    # juridique
    "avocat": "juridique",
    "cabinet d'avocats": "juridique",
    "droit": "juridique",
    # comptabilite
    "expert-comptable": "comptabilite",
    "cabinet comptable": "comptabilite",
    "finance": "comptabilite",
    # evenementiel
    "evenement": "evenementiel",
    "agence evenementielle": "evenementiel",
    # traduction
    "traduction technique": "traduction",
    "localisation": "traduction",
    # relation presse
    "rp": "relation_presse",
    "attache de presse": "relation_presse",
    # hors-ICP
    "hotel": "hotellerie",
    "restaurant": "restauration",
    "commerce": "retail",
    "commerce de detail": "retail",
    "boutique": "retail",
    "medical": "sante",
    "clinique": "sante",
    "pharmacie": "sante",
    # rôles
    "ceo": "fondateur",
    "founder": "fondateur",
    "co-fondateur": "fondateur",
    "cofondateur": "fondateur",
    "dirigeant": "decideur",
    "gerant": "decideur",
    "president": "decideur",
    "dg": "directeur",
    "directeur general": "directeur",
    "cto": "directeur",
    "cmo": "directeur",
    # Intitulés que Hunter rend le plus souvent au niveau `executive` : sans eux,
    # ils retombaient en titre brut et manquaient l'égalité stricte du scoring.
    "cfo": "directeur",
    "coo": "directeur",
    "director": "directeur",
    "managing partner": "decideur",
    "partner": "decideur",
    "associe": "decideur",
    "owner": "fondateur",
    "proprietaire": "fondateur",
    "head of": "manager",
    "responsable": "manager",
    "chef de projet": "manager",
    "charge de": "operationnel",
    "assistant": "operationnel",
    "stagiaire": "operationnel",
}


# --- Libellés de recherche par secteur ----------------------------------------
# Traduction d'un secteur canonique vers les termes à envoyer aux sources externes.
# Déterministe et versionné : une même config produit toujours les mêmes requêtes.
#   maps    → requêtes texte pour Google Places
#   apollo  → valeurs d'industrie de l'API Apollo (anglais)
#   hunter  → valeurs d'industrie de Hunter Discover
#
# ⚠️ La colonne `hunter` est une **liste fermée** : Hunter rejette (400
# `invalid_industry`) toute valeur absente de sa nomenclature. Les valeurs
# ci-dessous ont été vérifiées une à une contre l'API — elles suivent la
# nomenclature LinkedIn *révisée* (« Marketing Services », et non l'ancien
# « Marketing & Advertising » qu'utilise encore Apollo). D'où deux colonnes
# distinctes malgré leur ressemblance : elles ne sont pas interchangeables.
LIBELLES_RECHERCHE: dict[str, dict[str, tuple[str, ...]]] = {
    "marketing": {
        "maps": ("agence marketing", "agence de publicité"),
        "apollo": ("marketing & advertising", "advertising"),
        "hunter": ("Marketing Services", "Advertising Services"),
        "linkedin": ("marketing",),
    },
    "communication": {
        "maps": ("agence de communication", "agence de relations publiques"),
        "apollo": ("public relations & communications",),
        "hunter": ("Public Relations and Communications Services",),
        "linkedin": ("communication",),
    },
    "conseil": {
        "maps": ("cabinet de conseil", "société de conseil"),
        "apollo": ("management consulting",),
        "hunter": ("Business Consulting and Services",),
        "linkedin": ("conseil",),
    },
    "developpement": {
        "maps": ("agence de développement web", "société informatique"),
        "apollo": ("information technology & services", "computer software"),
        "hunter": ("IT Services and IT Consulting", "Software Development"),
        "linkedin": ("développement logiciel",),
    },
    "design": {
        "maps": ("studio de design", "agence de design graphique"),
        "apollo": ("design", "graphic design"),
        "hunter": ("Design Services", "Graphic Design"),
        "linkedin": ("design",),
    },
    "rh": {
        "maps": ("cabinet de recrutement", "agence de ressources humaines"),
        "apollo": ("human resources", "staffing & recruiting"),
        "hunter": ("Human Resources Services", "Staffing and Recruiting"),
        "linkedin": ("ressources humaines",),
    },
    "formation": {
        "maps": ("organisme de formation", "centre de formation professionnelle"),
        "apollo": ("professional training & coaching",),
        "hunter": ("Professional Training and Coaching",),
        "linkedin": ("formation professionnelle",),
    },
    "juridique": {
        "maps": ("cabinet d'avocats", "conseil juridique"),
        "apollo": ("legal services", "law practice"),
        "hunter": ("Legal Services", "Law Practice"),
        "linkedin": ("juridique",),
    },
    "comptabilite": {
        "maps": ("cabinet d'expertise comptable", "cabinet comptable"),
        "apollo": ("accounting",),
        "hunter": ("Accounting",),
        "linkedin": ("expertise comptable",),
    },
    "evenementiel": {
        "maps": ("agence événementielle", "organisation d'événements"),
        "apollo": ("events services",),
        "hunter": ("Events Services",),
        "linkedin": ("événementiel",),
    },
    "traduction": {
        "maps": ("agence de traduction", "services de traduction"),
        "apollo": ("translation & localization",),
        "hunter": ("Translation and Localization",),
        "linkedin": ("traduction",),
    },
    "relation_presse": {
        "maps": ("agence de relations presse", "attaché de presse"),
        "apollo": ("public relations & communications",),
        "hunter": ("Public Relations and Communications Services",),
        "linkedin": ("relations presse",),
    },
    # Secteurs hors services B2B : seule la colonne `hunter` est renseignée, car
    # Hunter est la seule source à exiger une valeur d'un catalogue fermé. Maps et
    # Apollo continuent de retomber sur la saisie du client, comme avant.
    "hotellerie": {"hunter": ("Hospitality",)},
    "restauration": {"hunter": ("Restaurants",)},
    "retail": {"hunter": ("Retail",)},
    "sante": {"hunter": ("Hospitals and Health Care",)},
    "immobilier": {"hunter": ("Real Estate",)},
    "industrie": {"hunter": ("Manufacturing",)},
    "transport": {"hunter": ("Transportation, Logistics, Supply Chain and Storage",)},
    "batiment": {"hunter": ("Construction", "Architecture and Planning")},
    "education": {"hunter": ("Education Administration Programs",)},
    "association": {"hunter": ("Non-profit Organizations",)},
}

# Intitulés de poste à cibler, par rôle canonique. Bilingue : les annuaires B2B
# indexent majoritairement en anglais, les sources francophones en français.
TITRES_PAR_ROLE: dict[str, tuple[str, ...]] = {
    "fondateur": ("Founder", "Co-Founder", "Owner", "Fondateur", "Cofondateur"),
    "decideur": ("CEO", "Managing Director", "President", "Gérant", "Dirigeant"),
    "directeur": ("Director", "Directeur Général", "Head of", "CTO", "CMO"),
    "manager": ("Manager", "Responsable", "Chef de projet"),
    "operationnel": ("Specialist", "Chargé de", "Consultant"),
}

# --- LinkedIn (via Apify) -----------------------------------------------------
# Tranches d'effectif de LinkedIn — **liste fermée de codes opaques**, vérifiée
# contre le schéma de l'acteur. Le piège : `A` ne vaut PAS « 1-10 » mais
# « Self-Employed ». Mapper naïvement A sur la première tranche décalerait tout
# le tableau d'un cran, silencieusement.
TRANCHES_LINKEDIN: tuple[tuple[str, int, int], ...] = (
    ("A", 1, 1),          # Self-Employed
    ("B", 1, 10),
    ("C", 11, 50),
    ("D", 51, 200),
    ("E", 201, 500),
    ("F", 501, 1000),
    ("G", 1001, 5000),
    ("H", 5001, 10000),
    ("I", 10001, 10**9),
)

# Niveaux hiérarchiques LinkedIn, par rôle canonique. Identifiants numériques
# fermés (`320` = Owner/Partner, `310` = CXO, `220` = Director…) : LinkedIn ne
# filtre pas sur des intitulés libres. Plusieurs niveaux par rôle, car la
# frontière entre « CXO » et « Owner » dépend de la façon dont la personne
# s'est déclarée, pas de sa fonction réelle.
SENIORITE_LINKEDIN_PAR_ROLE: dict[str, tuple[str, ...]] = {
    "fondateur": ("320",),               # Owner / Partner
    "decideur": ("310", "320"),          # CXO + Owner / Partner
    "directeur": ("220", "300"),         # Director + Vice President
    "manager": ("200", "210"),           # Entry Level Manager + Experienced Manager
    "operationnel": ("110", "120"),      # Entry Level + Senior
}


# Niveau hiérarchique Hunter, par rôle canonique. Hunter ne filtre pas sur des
# intitulés libres mais sur trois valeurs fermées — `junior`, `senior`, `executive`.
# Sans ce filtre, un domain-search rend indifféremment le PDG et le support.
SENIORITE_PAR_ROLE: dict[str, str] = {
    "fondateur": "executive",
    "decideur": "executive",
    "directeur": "executive",
    "manager": "senior",
    "operationnel": "junior",
}


def aplatir(valeur: str) -> str:
    """Minuscules, sans accents, espaces normalisés — clé de comparaison neutre."""
    texte = unicodedata.normalize("NFKD", valeur or "")
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    texte = texte.lower().strip()
    return re.sub(r"[\s_-]+", " ", texte)


# Tables dérivées, calculées une fois au chargement plutôt qu'à chaque appel.
_PAR_FORME_PLATE = {aplatir(c): c for c in SECTEURS + ROLES}
_MOTIF_SYNONYME = {s: re.compile(rf"\b{re.escape(s)}\b") for s in _SYNONYMES}
_MOTIF_CANON = {c: re.compile(rf"\b{re.escape(aplatir(c))}\b") for c in SECTEURS + ROLES}


@lru_cache(maxsize=4096)
def _normaliser(valeur: str, referentiel: tuple[str, ...]) -> str | None:
    """Rattache une valeur libre au référentiel, ou None si aucune correspondance.

    Mémorisée : la même valeur est normalisée plusieurs fois par requête (validation
    d'ICP, plan de recherche, filtrage des leads), toujours avec le même résultat.
    """
    plat = aplatir(valeur)
    if not plat:
        return None

    # 1. correspondance directe avec le référentiel
    canon = _PAR_FORME_PLATE.get(plat)
    if canon in referentiel:
        return canon

    # 2. le libellé est un synonyme, ou en contient un
    #    ("agence de marketing digital B2B" → marketing)
    #    Une égalité exacte satisfait toujours les frontières de mot, donc ce seul
    #    passage couvre aussi le cas « la valeur EST un synonyme ».
    for source, cible in _SYNONYMES.items():
        if cible in referentiel and _MOTIF_SYNONYME[source].search(plat):
            return cible

    # 3. le libellé contient un terme du référentiel
    for canon in referentiel:
        if _MOTIF_CANON[canon].search(plat):
            return canon

    return None


def normaliser_secteur(valeur: str) -> str | None:
    """`"Marketing digital"` → `"marketing"`. **None si hors catalogue.**

    À utiliser quand on a besoin de savoir si la valeur est *connue*
    (ex. trouver ses libellés de recherche). Pour stocker ou comparer, préférer
    `canoniser_secteur`, qui accepte les secteurs personnalisés.
    """
    return _normaliser(valeur, SECTEURS)


def canoniser_secteur(valeur: str) -> str:
    """Forme stable d'un secteur, **quel qu'il soit**. Ne rejette jamais.

    Un secteur du catalogue est ramené à sa valeur canonique ; un secteur
    personnalisé est simplement stabilisé (minuscules, sans accents, tirets bas).

        "Marketing digital"    → "marketing"           (catalogue)
        "Restauration rapide"  → "restauration_rapide" (personnalisé)

    C'est cette fonction que Big Data doit appeler sur `lead.secteur`, et l'Agent
    Builder sur l'ICP : la comparaison ne tient que si les deux côtés canonisent
    de la même façon.
    """
    return normaliser_secteur(valeur) or stabiliser(valeur)


def stabiliser(valeur: str) -> str:
    """Forme slug d'un libellé hors catalogue : `"Cabinet vétérinaire"` → `cabinet_veterinaire`."""
    return re.sub(r"[^a-z0-9]+", "_", aplatir(valeur)).strip("_")


def normaliser_role(valeur: str) -> str | None:
    """`"CEO"` → `"fondateur"`. None si hors référentiel."""
    return _normaliser(valeur, ROLES)


# --- Pays -----------------------------------------------------------------------
# Hunter Discover ne géolocalise pas du texte libre : il exige un code ISO-3166
# alpha-2. Cette table couvre les marchés visés par le produit ; tout autre pays
# reste atteignable en saisissant directement son code (« PT », « BR »…).
PAYS_ISO: dict[str, str] = {
    "france": "FR",
    "belgique": "BE",
    "belgium": "BE",
    "suisse": "CH",
    "switzerland": "CH",
    "luxembourg": "LU",
    "canada": "CA",
    "maroc": "MA",
    "morocco": "MA",
    "tunisie": "TN",
    "algerie": "DZ",
    "senegal": "SN",
    "cote d'ivoire": "CI",
    "madagascar": "MG",
    "allemagne": "DE",
    "germany": "DE",
    "espagne": "ES",
    "spain": "ES",
    "italie": "IT",
    "italy": "IT",
    "portugal": "PT",
    "pays bas": "NL",
    "netherlands": "NL",
    "royaume uni": "GB",
    "united kingdom": "GB",
    "angleterre": "GB",
    "irlande": "IE",
    "ireland": "IE",
    "etats unis": "US",
    "united states": "US",
    "usa": "US",
}


def code_pays(valeur: str) -> str | None:
    """Code ISO-3166 alpha-2 d'un pays saisi librement. **None si non reconnu.**

    Rendre None plutôt que deviner : un mauvais code ferait prospecter le mauvais
    pays en silence, ce qui est pire que ne pas filtrer du tout.
    """
    plat = aplatir(valeur)
    if not plat:
        return None
    # Déjà un code ISO : on fait confiance au client plutôt que de le limiter à
    # la table ci-dessus.
    if re.fullmatch(r"[a-z]{2}", plat):
        return plat.upper()
    return PAYS_ISO.get(plat)
