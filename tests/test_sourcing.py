"""Tests du sourcing : simulation sans clé, mapping des résultats, filtrage ICP."""
import asyncio

import httpx
import pytest

from app.schemas.ares import ICP
from app.schemas.builder import BlocRecherche
from app.schemas.sourcing import ExecuterPlanRequest
from app.sourcing.apollo import Apollo
from app.sourcing.base import construire_lead
from app.sourcing.executeur import executer_plan
from tests.conftest import HEADERS

WID = "11111111-1111-1111-1111-111111111111"

ICP_TYPE = dict(
    secteurs_inclus=["marketing", "conseil"],
    secteurs_exclus=["hotellerie"],
    taille_min=5,
    taille_max=30,
    roles_cibles=["fondateur"],
)


def _executer(**kwargs):
    """Exécute le plan de façon synchrone — évite une dépendance pytest-asyncio."""
    params = dict(workspace_id=WID, icp=ICP(**ICP_TYPE), sources=["apollo"], dry_run=True)
    params.update(kwargs)
    return asyncio.run(executer_plan(ExecuterPlanRequest(**params)))


# ----- Mode simulation -------------------------------------------------------
def test_dry_run_n_appelle_rien_et_montre_les_requetes():
    res = _executer()
    apollo = next(r for r in res.par_source if r.source == "apollo")
    assert apollo.statut == "simule"
    assert apollo.nb_leads == 0
    assert apollo.requetes[0].url.endswith("/mixed_people/api_search")
    assert res.leads == []


def test_dry_run_ne_divulgue_jamais_la_cle():
    """Tout paramètre d'authentification doit être masqué dans l'aperçu."""
    res = _executer(sources=["apollo", "google_maps"], zone="Lyon")
    params_auth = 0
    for source in res.par_source:
        for requete in source.requetes:
            for nom, valeur in requete.entetes.items():
                if "key" in nom.lower():
                    params_auth += 1
                    assert valeur == "***", f"{nom} n'est pas masqué"
    assert params_auth >= 2  # apollo + places


def test_dry_run_transmet_les_filtres_du_plan():
    res = _executer()
    # Apollo attend ses filtres en parametres d'URL, pas dans le corps.
    corps = next(r for r in res.par_source if r.source == "apollo").requetes[0].params
    assert corps["organization_num_employees_ranges"] == ["5,30"]
    assert "Founder" in corps["person_titles"]
    assert corps["per_page"] == 25


def test_limite_plafonne_la_taille_de_page():
    res = _executer(limite=200)
    corps = next(r for r in res.par_source if r.source == "apollo").requetes[0].params
    assert corps["per_page"] == 100  # plafond Apollo


# ----- Sources non branchées -------------------------------------------------
def test_sources_non_implementees_sont_declarees_explicitement():
    res = _executer(sources=["linkedin", "hunter", "site_web"])
    statuts = {r.source: r.statut for r in res.par_source}
    assert statuts == {
        "linkedin": "non_implemente",
        "hunter": "non_implemente",
        "site_web": "non_implemente",
    }
    assert all(r.erreur for r in res.par_source)


# ----- Clé absente -----------------------------------------------------------
def test_sans_cle_le_statut_est_explicite_pas_une_erreur():
    res = _executer(dry_run=False)
    apollo = next(r for r in res.par_source if r.source == "apollo")
    assert apollo.statut == "non_configuree"
    assert "APOLLO_API_KEY" in apollo.erreur


# ----- Plan invalide ---------------------------------------------------------
def test_icp_sans_secteur_n_execute_aucune_requete():
    # Ne pas dépenser d'appels d'API sur un plan qu'on sait vide.
    res = asyncio.run(executer_plan(
        ExecuterPlanRequest(
            workspace_id=WID, icp=ICP(roles_cibles=["fondateur"]), dry_run=False
        )
    ))
    assert res.par_source == []
    assert any(d.niveau == "erreur" for d in res.diagnostics)


# ----- Mapping vers le schéma Lead ------------------------------------------
def test_construire_lead_canonise_le_secteur():
    lead = construire_lead(nom="Studio Créa", secteur="Marketing digital", source="apollo")
    assert lead.secteur == "marketing"  # sinon le filtrage ICP échouerait


def test_construire_lead_canonise_un_secteur_personnalise():
    lead = construire_lead(nom="Vetocare", secteur="Cabinet vétérinaire", source="apollo")
    assert lead.secteur == "cabinet_veterinaire"


def test_construire_lead_normalise_le_role_mais_garde_le_titre_brut():
    lead = construire_lead(nom="A", titre_contact="CEO", source="apollo")
    assert lead.role_contact == "fondateur"
    assert lead.donnees_brutes["titre_brut"] == "CEO"


def test_construire_lead_conserve_un_titre_inconnu():
    lead = construire_lead(nom="A", titre_contact="Chief Vibes Officer", source="apollo")
    assert lead.role_contact == "Chief Vibes Officer"


def test_construire_lead_trace_la_provenance():
    lead = construire_lead(nom="A", source="google_maps")
    assert lead.donnees_brutes["sources"] == ["google_maps"]
    assert lead.donnees_brutes["collecte_le"]
    assert lead.ingested_at is not None


# ----- Exécution réelle (HTTP mocké) ----------------------------------------
def test_apollo_mappe_les_resultats(faux_http):
    charge = {
        "people": [
            {
                "name": "Marie Dupont",
                "title": "Founder",
                "email": "marie@studiocrea.co",
                "organization": {
                    "name": "Studio Créa",
                    "industry": "Marketing digital",
                    "estimated_num_employees": 18,
                    "website_url": "https://studiocrea.co",
                },
            },
            {"name": "Sans Entreprise", "title": "CEO"},  # ignoré : pas d'organisation
        ]
    }

    faux_http("apollo", charge=charge)

    leads = asyncio.run(
        Apollo("cle-de-test").executer(
            BlocRecherche(source="apollo", type="filtres", filtres={}), limite=10
        )
    )
    assert len(leads) == 1
    lead = leads[0]
    assert lead.nom == "Studio Créa"
    assert lead.secteur == "marketing"
    assert lead.taille_effectif == 18
    assert lead.role_contact == "fondateur"
    assert lead.contact["email"] == "marie@studiocrea.co"


def test_erreur_http_ne_fait_pas_tomber_l_execution(faux_http):
    faux_http("apollo", exception=httpx.ConnectError("réseau injoignable"))

    res = _executer(dry_run=False)
    apollo = next(r for r in res.par_source if r.source == "apollo")
    assert apollo.statut == "erreur"
    assert "ConnectError" in apollo.erreur


# ----- Filtrage des secteurs exclus -----------------------------------------
def test_les_leads_hors_icp_sont_ecartes(faux_http):
    charge = {
        "people": [
            {
                "title": "Founder",
                "organization": {"name": "Agence Nova", "industry": "marketing"},
            },
            {
                "title": "Founder",
                "organization": {"name": "Hôtel du Port", "industry": "hotellerie"},
            },
        ]
    }

    faux_http("apollo", charge=charge)

    res = _executer(dry_run=False)
    assert [lead.nom for lead in res.leads] == ["Agence Nova"]
    assert res.rejetes_hors_icp == 1


# ----- Endpoint --------------------------------------------------------------
def test_sourcing_endpoint(client):
    payload = {
        "workspace_id": WID,
        "icp": ICP_TYPE,
        "sources": ["apollo", "google_maps"],
        "zone": "Genève",
        "dry_run": True,
    }
    r = client.post("/api/v1/sourcing/executer", json=payload, headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert {s["source"] for s in body["par_source"]} == {"apollo", "google_maps"}
    assert all(s["statut"] == "simule" for s in body["par_source"])


def test_sourcing_exige_la_cle(client):
    assert client.post("/api/v1/sourcing/executer", json={}).status_code == 401
