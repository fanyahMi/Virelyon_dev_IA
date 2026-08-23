"""Interface commune aux connecteurs de sourcing.

Chaque connecteur sait faire deux choses :
- `apercu()` — décrire l'appel qu'il enverrait, **sans réseau** (mode `dry_run`) ;
- `executer()` — l'envoyer réellement et rendre des `Lead`.

Le mode `dry_run` n'est pas un gadget de test : c'est ce qui permet de démontrer
et de valider toute la chaîne **avant** d'avoir les clés d'API et le budget.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.builder.referentiels import canoniser_secteur, normaliser_role
from app.schemas.ares import Lead
from app.schemas.builder import BlocRecherche
from app.schemas.sourcing import RequeteHTTP

CLE_MASQUEE = "***"
TIMEOUT = httpx.Timeout(20.0)
# Overpass (OpenStreetMap) calcule ses requêtes à la volée : plus lent.
TIMEOUT_LONG = httpx.Timeout(90.0)


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

    async def executer(self, bloc: BlocRecherche, limite: int) -> list[Lead]:
        raise NotImplementedError


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
) -> Lead:
    """Mappe un résultat brut vers le schéma `Lead` attendu par ARES.

    Deux garanties :
    - le secteur passe par `canoniser_secteur()` — sans quoi le filtrage ICP
      échouerait sur des leads parfaitement valides ;
    - la provenance est tracée dans `donnees_brutes` (exigence de traçabilité).
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
        },
        ingested_at=horodatage,
    )
