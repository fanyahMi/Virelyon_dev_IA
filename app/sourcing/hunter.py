"""Connecteurs Hunter — découverte d'entreprises **et** email du décideur.

Hunter est la seule source du projet à couvrir les deux natures, avec une seule clé :

- **Discover** (`POST /v2/discover`) — DÉCOUVERTE : trouve des entreprises à partir
  de critères structurés (industrie, effectif, localisation du siège).
- **Domain Search** (`GET /v2/domain-search`) — ENRICHISSEMENT : sur un domaine déjà
  connu, rend les emails professionnels avec nom, poste et niveau hiérarchique.

Enchaînés, ils couvrent les quatre promesses du produit — trouver l'entreprise,
connaître sa taille, identifier le décideur, obtenir son email — là où Google Places
s'arrête au nom et au site web.

⚠️ La clé Hunter passe en **paramètre d'URL** (`api_key`), pas en en-tête : c'est la
seule source du projet dans ce cas. D'où le masquage explicite dans les aperçus —
un secret dans une URL fuite bien plus facilement (journaux d'accès, referers).
"""
from __future__ import annotations

import asyncio

import httpx

from app.builder.referentiels import normaliser_role
from app.schemas.ares import Lead
from app.schemas.builder import BlocRecherche
from app.schemas.sourcing import RequeteHTTP
from app.sourcing.base import (
    CLE_MASQUEE,
    TIMEOUT,
    Connecteur,
    construire_lead,
    domaine_de,
)

URL_DISCOVER = "https://api.hunter.io/v2/discover"
URL_DOMAIN_SEARCH = "https://api.hunter.io/v2/domain-search"

# Domaine fictif employé dans l'aperçu `dry_run` de l'enrichissement : à ce
# moment-là, aucune découverte n'a eu lieu, il n'existe donc aucun domaine réel.
# Un jeton visiblement factice vaut mieux qu'un exemple crédible qu'on croirait vrai.
DOMAINE_GABARIT = "{domaine-du-lead}"

# Appels d'enrichissement menés de front. Hunter limite le débit : enchaîner 200
# domaines un par un serait interminable, tous les lancer d'un coup se ferait rejeter.
ENRICHISSEMENTS_SIMULTANES = 5


def _params(cle: str, **autres) -> dict:
    """Paramètres d'URL. Une seule définition : l'aperçu et l'appel réel ne
    peuvent pas diverger — et donc l'aperçu ne peut pas oublier de masquer."""
    return {"api_key": cle, **{k: v for k, v in autres.items() if v}}


class HunterDiscover(Connecteur):
    """Découverte d'entreprises par critères — remplace la recherche cartographique.

    Limite structurelle à connaître : Discover ne rend que `domain`,
    `organization` et `emails_count` — **ni effectif ni décideur**, même quand on
    filtre dessus. Les leads qui en sortent sont donc incomplets tant que le
    domain-search n'est pas passé ; c'est le rôle de l'enrichissement qui suit.
    """

    source = "hunter_discover"
    variable_cle = "HUNTER_API_KEY"
    nature = "decouverte"

    def _corps_par_secteur(self, bloc: BlocRecherche) -> list[tuple[str, dict]]:
        """Une requête PAR SECTEUR : `(secteur canonique, corps JSON)`.

        Discover ne rappelle pas l'industrie trouvée dans sa réponse. Grouper
        plusieurs secteurs en un appel rendrait donc des leads dont on ne saurait
        pas dire le secteur — et le filtrage ICP repose dessus.

        ⚠️ Aucun `limit`/`offset` : ils sont réservés au plan Premium et font
        échouer la requête entière (`pagination_error`). Le plafond est appliqué
        côté client, sur les résultats.
        """
        filtres = bloc.filtres or {}
        commun: dict = {}
        if filtres.get("headcount"):
            commun["headcount"] = filtres["headcount"]
        if filtres.get("headquarters_location"):
            # Hunter enveloppe ses filtres : une liste nue est rejetée (400).
            commun["headquarters_location"] = {"include": filtres["headquarters_location"]}

        return [
            (secteur, {"industry": {"include": valeurs}, **commun})
            for secteur, valeurs in (filtres.get("industries_par_secteur") or {}).items()
        ]

    def apercu(self, bloc: BlocRecherche, limite: int) -> list[RequeteHTTP]:
        return [
            RequeteHTTP(
                methode="POST",
                url=URL_DISCOVER,
                entetes={"Content-Type": "application/json"},
                params=_params(CLE_MASQUEE),
                corps=corps,
            )
            for _, corps in self._corps_par_secteur(bloc)
        ]

    async def executer(self, bloc: BlocRecherche, limite: int) -> list[Lead]:
        requetes = self._corps_par_secteur(bloc)
        if not requetes:
            return []

        # Répartir le plafond entre les secteurs : sans ça, le premier secteur
        # consommerait la totalité et les suivants ne rendraient rien.
        part = max(1, -(-limite // len(requetes)))

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:

            async def chercher(secteur: str, corps: dict) -> list[Lead]:
                reponse = await client.post(
                    URL_DISCOVER, params=_params(self.cle), json=corps
                )
                reponse.raise_for_status()
                return _vers_leads(
                    _organisations(reponse.json().get("data")), secteur, part, self.source
                )

            groupes = await asyncio.gather(
                *(chercher(secteur, corps) for secteur, corps in requetes)
            )

        leads: list[Lead] = []
        vus: set[str] = set()
        for lead in (lead for groupe in groupes for lead in groupe):
            domaine = domaine_de(lead) or lead.nom
            if domaine in vus:
                continue  # un même domaine peut ressortir sur deux secteurs
            vus.add(domaine)
            leads.append(lead)
            if len(leads) >= limite:
                break
        return leads


class Hunter(Connecteur):
    """Enrichissement : l'email du décideur, à partir du domaine de l'entreprise."""

    source = "hunter"
    variable_cle = "HUNTER_API_KEY"
    nature = "enrichissement"

    def _params_domaine(self, cle: str, bloc: BlocRecherche, domaine: str) -> dict:
        filtres = bloc.filtres or {}
        return _params(
            cle,
            domain=domaine,
            # Hunter attend les niveaux hiérarchiques séparés par des virgules.
            seniority=",".join(filtres.get("seniority") or []),
            # `personal` écarte les boîtes génériques (contact@, info@) : on ne
            # peut pas personnaliser un message adressé à une boîte partagée.
            type="personal",
            # Sans nom, impossible d'écrire « Bonjour Marie » — l'adresse serait
            # collectée pour rien.
            required_field="full_name",
            limit=10,
        )

    def _requetes(
        self, bloc: BlocRecherche, domaines: list[str]
    ) -> list[RequeteHTTP]:
        return [
            RequeteHTTP(
                methode="GET",
                url=URL_DOMAIN_SEARCH,
                params=self._params_domaine(CLE_MASQUEE, bloc, domaine),
            )
            for domaine in domaines
        ]

    def apercu(self, bloc: BlocRecherche, limite: int) -> list[RequeteHTTP]:
        """Gabarit de l'appel. En `dry_run`, aucun lead n'a encore été découvert :
        on montre la forme de la requête, pas un domaine inventé."""
        return self._requetes(bloc, [DOMAINE_GABARIT])

    def apercu_enrichissement(
        self, bloc: BlocRecherche, limite: int, domaines: list[str]
    ) -> list[RequeteHTTP]:
        return self._requetes(bloc, domaines)

    async def enrichir(
        self, leads: list[Lead], bloc: BlocRecherche, limite: int
    ) -> int:
        """Ajoute l'email du décideur aux leads qui ont un domaine et pas d'email.

        Deux économies délibérées : on ignore les leads qui ont déjà un email
        (Apollo en fournit) et ceux sans domaine — chaque appel évité est un
        crédit Hunter conservé. `limite` plafonne le nombre d'appels : au-delà,
        les leads restants ne sont pas enrichis, seulement laissés en l'état.
        """
        candidats = [
            (lead, domaine)
            for lead, domaine in ((l, domaine_de(l)) for l in leads)
            if domaine and not (lead.contact or {}).get("email")
        ][:limite]
        if not candidats:
            return 0

        verrou = asyncio.Semaphore(ENRICHISSEMENTS_SIMULTANES)

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:

            async def traiter(lead: Lead, domaine: str) -> bool:
                async with verrou:
                    reponse = await client.get(
                        URL_DOMAIN_SEARCH,
                        params=self._params_domaine(self.cle, bloc, domaine),
                    )
                reponse.raise_for_status()
                donnees = reponse.json().get("data") or {}
                choix = _meilleur_email(
                    donnees.get("emails") or [], (bloc.filtres or {}).get("seniority") or []
                )
                if choix is None:
                    return False
                _appliquer(lead, choix, self.source)
                return True

            resultats = await asyncio.gather(
                *(traiter(lead, domaine) for lead, domaine in candidats)
            )

        return sum(resultats)


def _vers_leads(
    organisations: list[dict], secteur: str, limite: int, source: str
) -> list[Lead]:
    """Organisations Discover → `Lead`, avec le secteur **demandé**.

    Le secteur ne vient pas de la réponse (Discover ne le rend pas) mais du filtre
    qu'on a envoyé. Ce n'est pas une supposition : Hunter a lui-même restreint la
    recherche à cette industrie. La valeur brute est tracée dans `donnees_brutes`.
    """
    leads: list[Lead] = []
    for org in organisations[:limite]:
        nom = org.get("organization")
        domaine = org.get("domain")
        if not nom:
            continue  # sans nom d'entreprise, le lead n'est pas exploitable
        leads.append(
            construire_lead(
                nom=nom,
                secteur=secteur,
                # Le domaine est la clé de l'enrichissement qui suit : on le range
                # comme site web, la forme que `domaine_de()` sait relire.
                site_web=f"https://{domaine}" if domaine else None,
                source=source,
                # Nombre d'emails que Hunter connaît sur ce domaine : indique
                # d'avance si le domain-search a des chances d'aboutir.
                extras={"emails_connus": org.get("emails_count")},
            )
        )
    return leads


def _organisations(donnees) -> list[dict]:
    """Organisations rendues par Discover, quelle que soit l'enveloppe.

    Hunter range tantôt les résultats sous `data.organizations`, tantôt
    directement dans `data`. Accepter les deux formes évite que le connecteur
    dépende d'un détail de sérialisation non versionné.
    """
    if isinstance(donnees, dict):
        return donnees.get("organizations") or []
    return donnees if isinstance(donnees, list) else []


def _meilleur_email(emails: list[dict], seniorites: list[str]) -> dict | None:
    """L'email le plus pertinent : le décideur visé, à défaut le plus fiable.

    Hunter rend tous les emails d'un domaine, du dirigeant au support. Prendre le
    premier reviendrait à écrire à n'importe qui : on retient d'abord les niveaux
    hiérarchiques demandés par l'ICP, puis on tranche par indice de confiance.
    """
    utilisables = [e for e in emails if e.get("value")]
    if not utilisables:
        return None
    vises = [e for e in utilisables if e.get("seniority") in seniorites]
    return max(vises or utilisables, key=lambda e: e.get("confidence") or 0)


def _appliquer(lead: Lead, email: dict, source: str) -> None:
    """Reporte l'email retenu sur le lead, sans écraser ce qui existe déjà.

    L'enrichissement complète, il n'arbitre pas : un rôle déjà identifié par une
    source de découverte est plus sûr qu'un intitulé déduit d'un annuaire d'emails.
    """
    lead.contact = dict(lead.contact or {})
    lead.contact["email"] = email["value"]

    nom = " ".join(
        part for part in (email.get("first_name"), email.get("last_name")) if part
    )
    if nom:
        lead.contact.setdefault("nom", nom)

    poste = email.get("position")
    if poste and not lead.role_contact:
        # Comme à la collecte : on garde l'intitulé d'origine si aucun rôle
        # canonique ne correspond — l'écraser serait une perte d'information.
        lead.role_contact = normaliser_role(poste) or poste

    brutes = lead.donnees_brutes = dict(lead.donnees_brutes or {})
    sources = list(brutes.get("sources") or [])
    if source not in sources:
        sources.append(source)
    brutes["sources"] = sources
    brutes["confiance_email"] = email.get("confidence")
    if poste and not brutes.get("titre_brut"):
        brutes["titre_brut"] = poste
