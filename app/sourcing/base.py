"""Interface commune aux connecteurs de sourcing.

Chaque connecteur sait décrire l'appel qu'il enverrait — `apercu()`, **sans
réseau** (mode `dry_run`) — puis l'envoyer. Ce qu'il envoie dépend de sa nature :

- **découverte** : `executer()` cherche des entreprises inconnues et rend des `Lead` ;
- **enrichissement** : `enrichir()` complète **sur place** des leads déjà trouvés
  (email, décideur) et rend le nombre de leads réellement complétés.

Le mode `dry_run` n'est pas un gadget de test : c'est ce qui permet de démontrer
et de valider toute la chaîne **avant** d'avoir les clés d'API et le budget.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from app.builder.referentiels import canoniser_secteur, normaliser_role
from app.schemas.ares import Lead
from app.schemas.builder import BlocRecherche
from app.schemas.sourcing import RequeteHTTP

CLE_MASQUEE = "***"
TIMEOUT = httpx.Timeout(20.0)


class Connecteur:
    """Classe de base. Un connecteur = une source externe."""

    source: str = ""
    # Nom de la variable d'environnement portant la clé (pour les messages d'erreur).
    variable_cle: str = ""
    # "decouverte" (trouve des entreprises inconnues) ou "enrichissement"
    # (complète une entreprise déjà trouvée).
    nature: str = "decouverte"
    # Renseigné quand le connecteur n'est pas encore écrit : l'exécuteur s'en sert
    # pour déclarer la source plutôt que de l'ignorer silencieusement.
    motif_non_implemente: str | None = None

    def __init__(self, cle: str = "") -> None:
        self.cle = cle

    def configure(self) -> bool:
        return bool(self.cle)

    def apercu(self, bloc: BlocRecherche, limite: int) -> list[RequeteHTTP]:
        raise NotImplementedError

    # --- Découverte ---------------------------------------------------------
    async def executer(self, bloc: BlocRecherche, limite: int) -> list[Lead]:
        raise NotImplementedError

    # --- Enrichissement -----------------------------------------------------
    def apercu_enrichissement(
        self, bloc: BlocRecherche, limite: int, domaines: list[str]
    ) -> list[RequeteHTTP]:
        """Appels qui seraient envoyés pour ces domaines précis.

        Distinct d'`apercu()` : un enrichisseur ne sait quoi appeler qu'une fois
        la découverte faite. `apercu()` ne peut donc en montrer que le gabarit.
        """
        raise NotImplementedError

    async def enrichir(
        self, leads: list[Lead], bloc: BlocRecherche, limite: int
    ) -> int:
        """Complète les leads **sur place**. Rend le nombre de leads enrichis.

        Modifier sur place plutôt que rendre une nouvelle liste : plusieurs
        enrichisseurs se succèdent sur les mêmes leads, et l'ordre d'exécution ne
        doit pas changer l'identité des objets que l'appelant tient déjà.
        """
        raise NotImplementedError


def domaine_de(lead: Lead) -> str | None:
    """Domaine exploitable d'un lead, extrait de son site web.

    Les API d'enrichissement n'interrogent que des domaines nus : sans site web,
    un lead ne peut pas être enrichi. `https://www.studio-crea.co/fr/contact`
    devient `studio-crea.co`.
    """
    url = ((lead.contact or {}).get("site_web") or "").strip()
    if not url:
        return None
    # Sans schéma, `urlsplit` range tout dans `path` : on force la forme réseau.
    hote = urlsplit(url if "//" in url else f"//{url}").netloc
    hote = hote.split("@")[-1].split(":")[0].strip().lower()
    if hote.startswith("www."):
        hote = hote[4:]
    return hote or None


def construire_lead(
    *,
    nom: str,
    secteur: str | None = None,
    taille_effectif: int | None = None,
    titre_contact: str | None = None,
    email: str | None = None,
    site_web: str | None = None,
    nom_contact: str | None = None,
    source: str = "",
    extras: dict | None = None,
) -> Lead:
    """Mappe un résultat brut vers le schéma `Lead` attendu par ARES.

    Deux garanties :
    - le secteur passe par `canoniser_secteur()` — sans quoi le filtrage ICP
      échouerait sur des leads parfaitement valides ;
    - la provenance est tracée dans `donnees_brutes` (exigence de traçabilité).

    `extras` complète `donnees_brutes` avec ce que la source rend de particulier
    (ex. une tranche d'effectif plutôt qu'un nombre) : le détail d'origine ne doit
    pas se perdre dans la normalisation.
    """
    horodatage = datetime.now(timezone.utc)
    contact: dict = {}
    if email:
        contact["email"] = email
    if nom_contact:
        contact["nom"] = nom_contact
    if site_web:
        contact["site_web"] = site_web

    role = normaliser_role(titre_contact) if titre_contact else None

    return Lead(
        nom=nom,
        secteur=canoniser_secteur(secteur) if secteur else None,
        taille_effectif=taille_effectif,
        # On garde l'intitulé d'origine si aucun rôle canonique ne correspond :
        # la qualification saura l'interpréter, l'écraser serait une perte.
        role_contact=role or (titre_contact or None),
        contact=contact,
        donnees_brutes={
            "collecte_le": horodatage.isoformat(),
            "sources": [source] if source else [],
            "titre_brut": titre_contact,
            "secteur_brut": secteur,
            "signaux_bruts": [],
            **(extras or {}),
        },
        ingested_at=horodatage,
    )
