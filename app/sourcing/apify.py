"""Connecteur Apify — LinkedIn, via l'acteur `harvestapi/linkedin-profile-search`.

Apify n'est pas une source : c'est un **exécutant**. On lui confie un « acteur »
qui va scraper LinkedIn, faute d'API de recherche. C'est ce qui rend cette source
possible — et c'est aussi ce qui la rend juridiquement à part : voir
`_AVERTISSEMENT_LINKEDIN` dans `builder/plan_recherche.py`, remonté au client dans
la réponse plutôt que noyé ici.

**Mécanique.** `run-sync-get-dataset-items` lance l'acteur et rend directement ses
résultats, ce qui évite d'avoir à sonder l'état d'une exécution. En contrepartie
l'appel est long — des dizaines de secondes, pas les 20 s du `TIMEOUT` commun.

**Points de contrat vérifiés contre l'API — aucun n'est dans la documentation :**
- `currentPosition` est **souvent vide** alors que la personne a bien un poste :
  2 profils sur 3 lors de la vérification. Le poste réel est dans `experience[0]`.
  S'en remettre à `currentPosition` seul jetterait la majorité des leads valides.
- `emails` est une **liste d'objets** `{email, status, qualityScore}`, pas une
  chaîne. `status` vaut `valid` ou `risky` — un `risky` sur domaine attrape-tout
  n'est pas une adresse sur laquelle envoyer, on le garde mais on l'étiquette.
- `companyWebsites[]` donne le domaine de l'entreprise : c'est lui qui permet à
  l'enrichissement Hunter d'atteindre ensuite ces leads.
- Apify **refuse** (400 `max-total-charge-usd-below-minimum`) tout plafond de
  dépense inférieur à 0,10 $. Un plafond calculé trop juste fait échouer l'appel
  entier, pas seulement la partie non financée.
- Le plafond s'applique **avant** la collecte : trop bas, l'exécution réussit
  (HTTP 201) en ne rendant **aucun** résultat, après avoir facturé la recherche.
- L'acteur **réserve la page suivante** avant de s'engager sur la première. Un
  plafond qui ne couvre qu'une page plus les profils demandés est refusé : le
  journal affiche `chargeableWithinLimit: {'search-page': 0}` puis
  « max charge reached », et le run rend 0 profil en ayant facturé la recherche.
  Constaté à 0,16 $ (échec) contre 0,25 $ (succès) sur une requête identique de
  3 profils — d'où la page de réserve dans `plafond_depense()`.
"""
from __future__ import annotations

import asyncio

import httpx

from app.schemas.ares import Lead
from app.schemas.builder import BlocRecherche
from app.schemas.sourcing import RequeteHTTP
from app.sourcing.base import CLE_MASQUEE, Connecteur, construire_lead

URL = "https://api.apify.com/v2/acts/{acteur}/run-sync-get-dataset-items"
ACTEUR_LINKEDIN = "harvestapi~linkedin-profile-search"

# L'acteur tourne pour de vrai (navigateur, réseau, validation d'emails) : ~30 s
# pour 3 profils. Le TIMEOUT commun de 20 s couperait toutes les exécutions utiles.
TIMEOUT = httpx.Timeout(600.0, connect=20.0)

# Tarifs de l'acteur, en dollars (relevés dans son schéma).
PRIX_PAGE_RECHERCHE = 0.10   # une page = jusqu'à 25 profils
PRIX_PROFIL = 0.004
PRIX_PROFIL_AVEC_EMAIL = 0.01
PROFILS_PAR_PAGE = 25
# Page que l'acteur exige d'avoir en réserve pour accepter de scraper la
# première. Sans elle il s'arrête sur « max charge reached » sans rien rendre.
PAGES_DE_RESERVE = 1
# Plancher imposé par Apify : en dessous, l'appel est rejeté.
CHARGE_MINIMALE = 0.10


class ApifyLinkedIn(Connecteur):
    """Recherche de décideurs sur LinkedIn — découverte.

    Ce que cette source **ne rend pas**, et qu'il ne faut donc pas espérer :
    - **l'effectif** : on filtre dessus, il ne revient pas dans la réponse ;
    - **le secteur** : la recherche est floue (mots-clés), pas taxonomique. Une
      requête « marketing » ramène aussi une académie de tennis — constaté lors de
      la vérification. Attribuer le secteur recherché, comme on le fait pour
      Hunter dont le filtre est une nomenclature fermée, empoisonnerait le
      filtrage ICP et le `fit` du scoring. On laisse donc `secteur` vide et on
      note la requête d'origine : c'est `/ares/qualify` qui tranchera.
    """

    source = "linkedin"
    variable_cle = "APIFY_API_TOKEN"
    nature = "decouverte"

    def __init__(self, cle: str = "", chercher_emails: bool = True) -> None:
        super().__init__(cle)
        # Sans email, un lead LinkedIn n'est joignable qu'au prix d'un
        # enrichissement Hunter — deux fois payant. La recherche d'email de
        # l'acteur coûte 0,006 $ de plus par profil et évite ce détour.
        self.chercher_emails = chercher_emails

    # --- Construction de l'appel ---------------------------------------------
    def _mode(self) -> str:
        return "Full + email search" if self.chercher_emails else "Full"

    def _prix_profil(self) -> float:
        return PRIX_PROFIL_AVEC_EMAIL if self.chercher_emails else PRIX_PROFIL

    def plafond_depense(self, part: int) -> float:
        """Plafond de dépense d'UNE exécution, déduit du volume demandé.

        Calculé plutôt que configuré : le plafond doit suivre `limite`, sinon il
        protège d'un débordement qu'on n'a pas demandé et bloque une collecte
        qu'on a demandée.

        La **page de réserve** n'est pas de la prudence : sans elle l'acteur
        refuse de démarrer (voir l'en-tête du module). Un plafond n'est qu'un
        plafond — le réserver ne le dépense pas ; seuls les événements
        réellement facturés comptent.
        """
        pages = max(1, -(-part // PROFILS_PAR_PAGE))
        estimation = (
            (pages + PAGES_DE_RESERVE) * PRIX_PAGE_RECHERCHE
            + part * self._prix_profil()
        )
        # Une marge de 20 % : le plafond coupe la collecte AVANT de rendre quoi
        # que ce soit, donc l'estimer au plus juste revient à ne rien recevoir.
        estimation *= 1.2
        return max(CHARGE_MINIMALE, round(estimation + 0.005, 2))

    def _entrees(self, bloc: BlocRecherche, limite: int) -> list[tuple[str, dict, int]]:
        """`(secteur, entrée de l'acteur, part du plafond)`, une par secteur.

        L'acteur ne prend qu'un `searchQuery` : plusieurs secteurs imposent
        plusieurs exécutions. Le plafond de volume est réparti entre elles, sinon
        le premier secteur consommerait tout et les suivants ne rendraient rien.
        """
        filtres = bloc.filtres or {}
        requetes = filtres.get("requetes_par_secteur") or {}
        if not requetes:
            return []

        part = max(1, -(-limite // len(requetes)))
        commun = {
            cle: filtres[cle]
            for cle in ("currentJobTitles", "companyHeadcount", "seniorityLevelIds",
                        "locations")
            if filtres.get(cle)
        }
        return [
            (
                secteur,
                {
                    "profileScraperMode": self._mode(),
                    "searchQuery": terme,
                    "maxItems": part,
                    **commun,
                },
                part,
            )
            for secteur, terme in requetes.items()
        ]

    def apercu(self, bloc: BlocRecherche, limite: int) -> list[RequeteHTTP]:
        return [
            RequeteHTTP(
                methode="POST",
                url=URL.format(acteur=ACTEUR_LINKEDIN),
                # Le jeton passe en en-tête, jamais en paramètre d'URL : un secret
                # dans une URL fuite par les journaux d'accès et les referers.
                entetes={"Content-Type": "application/json",
                         "Authorization": f"Bearer {CLE_MASQUEE}"},
                params={"maxItems": part,
                        "maxTotalChargeUsd": self.plafond_depense(part)},
                corps=entree,
            )
            for _, entree, part in self._entrees(bloc, limite)
        ]

    # --- Exécution ------------------------------------------------------------
    async def executer(self, bloc: BlocRecherche, limite: int) -> list[Lead]:
        entrees = self._entrees(bloc, limite)
        if not entrees:
            return []

        entetes = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {self.cle}"}

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:

            async def chercher(secteur: str, entree: dict, part: int) -> list[Lead]:
                reponse = await client.post(
                    URL.format(acteur=ACTEUR_LINKEDIN),
                    headers=entetes,
                    params={"maxItems": part,
                            "maxTotalChargeUsd": self.plafond_depense(part)},
                    json=entree,
                )
                reponse.raise_for_status()
                donnees = reponse.json()
                # Une erreur applicative d'Apify arrive en objet là où un succès
                # rend une liste : sans ce test, `for profil in donnees` itérerait
                # sur les clés du message d'erreur.
                if not isinstance(donnees, list):
                    return []
                return [
                    lead
                    for profil in donnees[:part]
                    if (lead := _vers_lead(profil, entree.get("searchQuery", "")))
                ]

            groupes = await asyncio.gather(
                *(chercher(secteur, entree, part) for secteur, entree, part in entrees)
            )

        leads: list[Lead] = []
        vus: set[str] = set()
        for lead in (lead for groupe in groupes for lead in groupe):
            # Un même décideur peut ressortir sur deux requêtes de secteur.
            identite = (lead.contact or {}).get("linkedin") or lead.nom
            if identite in vus:
                continue
            vus.add(identite)
            leads.append(lead)
            if len(leads) >= limite:
                break
        return leads


def _poste(profil: dict) -> dict:
    """Poste actuel de la personne.

    `currentPosition` est vide dans la majorité des profils, alors même que le
    poste figure dans `experience[0]`. Le repli n'est pas défensif : c'est le cas
    courant.
    """
    for cle in ("currentPosition", "experience"):
        entrees = profil.get(cle) or []
        if entrees:
            return entrees[0]
    return {}


def _email(profil: dict) -> tuple[str | None, str | None]:
    """`(adresse, statut)` la plus sûre. Les `valid` passent avant les `risky`.

    On garde une adresse `risky` plutôt que de la jeter — c'est souvent un domaine
    attrape-tout, donc une piste réelle — mais son statut voyage avec elle : ARES
    ne doit pas la traiter comme une adresse vérifiée.
    """
    adresses = [e for e in (profil.get("emails") or []) if e.get("email")]
    if not adresses:
        return None, None
    meilleure = max(
        adresses,
        key=lambda e: (e.get("status") == "valid", e.get("qualityScore") or 0),
    )
    return meilleure["email"], meilleure.get("status")


def _vers_lead(profil: dict, requete: str) -> Lead | None:
    """Profil LinkedIn → `Lead`. `None` si la personne n'a pas d'entreprise.

    Un profil sans employeur actuel n'est pas un prospect B2B : il n'y a rien à
    qui vendre. Mieux vaut l'écarter ici que le laisser encombrer la
    qualification, qui coûte un appel LLM.
    """
    poste = _poste(profil)
    entreprise = (poste.get("companyName") or "").strip()
    if not entreprise:
        return None

    email, statut_email = _email(profil)
    sites = profil.get("companyWebsites") or []
    lieu = (profil.get("location") or {}).get("parsed") or {}
    prenom_nom = " ".join(
        p for p in (profil.get("firstName"), profil.get("lastName")) if p
    ).strip()

    lead = construire_lead(
        nom=entreprise,
        # Volontairement absent : la recherche floue ne permet pas de l'affirmer.
        secteur=None,
        # Volontairement absent : on filtre sur l'effectif, l'acteur ne le rend pas.
        taille_effectif=None,
        titre_contact=poste.get("position") or profil.get("headline"),
        email=email,
        site_web=(sites[0].get("url") if sites else None),
        nom_contact=prenom_nom or None,
        source="linkedin",
        extras={
            "secteur_recherche": requete,
            "statut_email": statut_email,
            "linkedin_url": profil.get("linkedinUrl"),
            "headline": profil.get("headline"),
            "ville": lieu.get("city"),
            "pays": lieu.get("countryCode"),
            "domaines_entreprise": [s.get("domain") for s in sites if s.get("domain")],
        },
    )
    if profil.get("linkedinUrl"):
        lead.contact["linkedin"] = profil["linkedinUrl"]
    return lead
