"""Connecteur Apollo — la source la plus complète pour la prospection B2B.

Elle couvre à elle seule les quatre promesses du produit : trouver l'entreprise,
connaître sa taille, identifier le décideur, obtenir un email professionnel.

Paramètres vérifiés contre la documentation Apollo (août 2026) :
- endpoint `mixed_people/api_search` (et non `mixed_people/search`) ;
- les filtres passent en **paramètres d'URL**, pas dans le corps de la requête ;
- authentification par en-tête `x-api-key` ;
- `person_titles`, `organization_num_employees_ranges` (format « 5,30 »), `per_page` (max 100).

Réserve assumée : le filtrage par industrie d'Apollo repose sur des identifiants
de tags qu'il faut interroger au préalable. On utilise donc `q_organization_keyword_tags`,
une recherche par mots-clés — moins précise mais utilisable sans table de
correspondance. À affiner une fois la clé disponible.
"""
from __future__ import annotations

import httpx

from app.schemas.ares import Lead
from app.schemas.builder import BlocRecherche
from app.schemas.sourcing import RequeteHTTP
from app.sourcing.base import CLE_MASQUEE, TIMEOUT, Connecteur, construire_lead

URL = "https://api.apollo.io/api/v1/mixed_people/api_search"


class Apollo(Connecteur):
    source = "apollo"
    variable_cle = "APOLLO_API_KEY"

    def _entetes(self, cle: str) -> dict:
        """Une seule définition : l'aperçu et l'appel réel ne peuvent pas diverger."""
        return {"Content-Type": "application/json", "Cache-Control": "no-cache",
                "x-api-key": cle}

    def _params(self, bloc: BlocRecherche, limite: int) -> dict:
        """Filtres Apollo, en paramètres d'URL comme l'exige l'API."""
        filtres = bloc.filtres or {}
        params: dict = {"page": 1, "per_page": min(limite, 100)}
        if filtres.get("organization_industries"):
            params["q_organization_keyword_tags"] = filtres["organization_industries"]
        for cle in ("organization_num_employees_ranges", "person_titles"):
            if filtres.get(cle):
                params[cle] = filtres[cle]
        return params

    def apercu(self, bloc: BlocRecherche, limite: int) -> list[RequeteHTTP]:
        return [
            RequeteHTTP(
                methode="POST",
                url=URL,
                entetes=self._entetes(CLE_MASQUEE),
                params=self._params(bloc, limite),
            )
        ]

    async def executer(self, bloc: BlocRecherche, limite: int) -> list[Lead]:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            reponse = await client.post(
                URL, headers=self._entetes(self.cle), params=self._params(bloc, limite)
            )
            reponse.raise_for_status()
            data = reponse.json()

        leads: list[Lead] = []
        for personne in (data.get("people") or [])[:limite]:
            org = personne.get("organization") or {}
            nom = org.get("name") or personne.get("organization_name")
            if not nom:
                continue  # sans entreprise, le lead n'est pas exploitable
            leads.append(
                construire_lead(
                    nom=nom,
                    secteur=org.get("industry"),
                    taille_effectif=org.get("estimated_num_employees"),
                    titre_contact=personne.get("title"),
                    email=personne.get("email"),
                    site_web=org.get("website_url"),
                    nom_contact=personne.get("name"),
                    source=self.source,
                )
            )
        return leads
