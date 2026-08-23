"""Connecteur OpenStreetMap — découverte locale, réelle, gratuite et sans clé.

Pourquoi cette source existe : Apollo réserve son API aux plans payants et Google
Places exige un compte de facturation. OpenStreetMap n'exige rien — ses données
sont publiques et librement réutilisables (licence ODbL, attribution requise).

Ce qu'elle rend :  nom, activité, site web, email et téléphone quand ils sont
renseignés par les contributeurs.
Ce qu'elle ne rend pas :  effectif ni décideur — aucune donnée de ce type dans
OSM. Les leads qui en sortent sont donc incomplets par nature, et leur score de
complétude s'en ressent. C'est le prix d'une source gratuite, pas un défaut.

Requêtes en Overpass QL sur https://overpass-api.de.
"""
from __future__ import annotations

import httpx

from app.schemas.ares import Lead
from app.schemas.builder import BlocRecherche
from app.schemas.sourcing import RequeteHTTP
from app.sourcing.base import TIMEOUT_LONG, Connecteur, construire_lead

URL = "https://overpass-api.de/api/interpreter"
# Overpass refuse les requêtes anonymes (406) : un agent explicite est exigé.
AGENT = "virelyon-ares/0.1 (sourcing B2B)"

# Secteur canonique → valeurs du tag `office` d'OpenStreetMap.
# C'est l'équivalent de LIBELLES_RECHERCHE pour cette source.
TAGS_PAR_SECTEUR: dict[str, tuple[str, ...]] = {
    "marketing": ("advertising_agency", "marketing"),
    "communication": ("advertising_agency", "newspaper"),
    "conseil": ("consulting", "research"),
    "developpement": ("it", "telecommunication"),
    "design": ("graphic_design", "architect"),
    "rh": ("employment_agency",),
    "formation": ("educational_institution",),
    "juridique": ("lawyer", "notary"),
    "comptabilite": ("accountant", "financial", "tax_advisor"),
    "evenementiel": ("event_management",),
    "traduction": ("translator",),
    "relation_presse": ("newspaper",),
    "immobilier": ("estate_agent",),
    "assurance": ("insurance",),
}


class OpenStreetMap(Connecteur):
    source = "openstreetmap"
    # Aucune clé : `configure()` doit renvoyer True même sans variable d'env.
    variable_cle = ""

    def configure(self) -> bool:
        return True

    def _tags(self, bloc: BlocRecherche) -> list[str]:
        """Tags OSM déduits des secteurs de l'ICP, via le plan de recherche."""
        tags: list[str] = []
        for secteur in bloc.filtres.get("secteurs") or []:
            for tag in TAGS_PAR_SECTEUR.get(secteur, ()):
                if tag not in tags:
                    tags.append(tag)
        return tags

    def _requete_ql(self, bloc: BlocRecherche, limite: int) -> str:
        zone = (bloc.filtres.get("zone") or "").strip()
        tags = self._tags(bloc)
        if not tags or not zone:
            return ""
        motif = "|".join(tags)
        # `out center` rend un point même pour les bâtiments (way), pas juste les nœuds.
        return (
            f'[out:json][timeout:25];\n'
            f'area["name"="{zone}"]["boundary"="administrative"]->.z;\n'
            f'( node["office"~"{motif}"](area.z);\n'
            f'  way["office"~"{motif}"](area.z); );\n'
            f"out center {min(limite, 100)};"
        )

    def apercu(self, bloc: BlocRecherche, limite: int) -> list[RequeteHTTP]:
        requete = self._requete_ql(bloc, limite)
        if not requete:
            return []
        return [
            RequeteHTTP(
                methode="POST",
                url=URL,
                entetes={"User-Agent": AGENT},
                corps={"data": requete},
            )
        ]

    async def executer(self, bloc: BlocRecherche, limite: int) -> list[Lead]:
        requete = self._requete_ql(bloc, limite)
        if not requete:
            return []

        async with httpx.AsyncClient(timeout=TIMEOUT_LONG) as client:
            reponse = await client.post(
                URL, data={"data": requete}, headers={"User-Agent": AGENT}
            )
            reponse.raise_for_status()
            elements = reponse.json().get("elements") or []

        # Un tag `office` peut correspondre à plusieurs secteurs : on remonte au
        # secteur canonique pour que le filtrage ICP fonctionne.
        secteur_du_tag = {
            tag: secteur
            for secteur, tags in TAGS_PAR_SECTEUR.items()
            for tag in tags
        }

        leads: list[Lead] = []
        vus: set[str] = set()
        for element in elements:
            tags = element.get("tags") or {}
            nom = tags.get("name")
            if not nom or nom in vus:
                continue  # sans nom, le lead n'est pas exploitable
            vus.add(nom)
            leads.append(
                construire_lead(
                    nom=nom,
                    secteur=secteur_du_tag.get(tags.get("office", "")),
                    email=tags.get("email") or tags.get("contact:email"),
                    site_web=tags.get("website") or tags.get("contact:website"),
                    source=self.source,
                )
            )
            if len(leads) >= limite:
                break
        return leads
