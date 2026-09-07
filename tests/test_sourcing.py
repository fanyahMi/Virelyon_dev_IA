"""Tests du sourcing : simulation sans clé, mapping des résultats, filtrage ICP."""
import asyncio
import re

import httpx
import pytest

from app.schemas.ares import ICP, Lead
from app.schemas.builder import BlocRecherche
from app.schemas.sourcing import ExecuterPlanRequest
from app.sourcing.apollo import Apollo
from app.sourcing.base import construire_lead, domaine_de
from app.sourcing.executeur import executer_plan
from app.sourcing.hunter import Hunter, HunterDiscover
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


def _porte_une_cle(nom: str) -> bool:
    """« key » comme mot entier — pas comme fragment.

    `x-api-key` et `api_key` en portent une ; `q_organization_keyword_tags`,
    filtre métier d'Apollo, n'en porte pas.
    """
    return "key" in re.split(r"[^a-z0-9]+", nom.lower())


def test_dry_run_ne_divulgue_jamais_la_cle():
    """Tout paramètre d'authentification doit être masqué dans l'aperçu.

    En-têtes ET paramètres d'URL : Hunter passe sa clé en `?api_key=`, où un
    secret fuite d'autant plus facilement (journaux d'accès, referers).
    """
    res = _executer(
        sources=["apollo", "google_maps", "hunter_discover", "hunter"], zone="Lyon"
    )
    params_auth = 0
    for source in res.par_source:
        for requete in source.requetes:
            for origine in (requete.entetes, requete.params):
                for nom, valeur in origine.items():
                    if _porte_une_cle(nom):
                        params_auth += 1
                        assert valeur == "***", f"{nom} n'est pas masqué"
    assert params_auth >= 4  # apollo + places + les deux endpoints Hunter


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
    """`site_web` reste le seul connecteur non écrit : il attend l'extraction LLM.

    LinkedIn n'en fait plus partie depuis qu'Apify l'exécute.
    """
    res = _executer(sources=["site_web"])
    site_web = next(r for r in res.par_source if r.source == "site_web")
    assert site_web.statut == "non_implemente"
    assert site_web.erreur


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


# ----- Hunter Discover : découverte -----------------------------------------
def _bloc_discover(**filtres):
    return BlocRecherche(source="hunter_discover", type="filtres", filtres=filtres)


# Forme réelle de la réponse Discover : ni industrie ni effectif n'y figurent.
CHARGE_DISCOVER = {
    "data": [
        {
            "domain": "studiocrea.co",
            "organization": "Studio Créa",
            "emails_count": {"personal": 12, "generic": 2, "total": 14},
        },
        {"domain": "sans-nom.co"},  # ignoré : pas de nom d'entreprise
    ]
}


def test_discover_attribue_le_secteur_demande(faux_http):
    # Discover ne rend pas l'industrie : le secteur est celui qu'on a filtré.
    faux_http("hunter", charge=CHARGE_DISCOVER)

    leads = asyncio.run(
        HunterDiscover("cle-de-test").executer(
            _bloc_discover(industries_par_secteur={"marketing": ["Marketing Services"]}),
            limite=10,
        )
    )
    assert len(leads) == 1
    lead = leads[0]
    assert lead.nom == "Studio Créa"
    assert lead.secteur == "marketing"
    # Le domaine devient le site web : c'est la clé de l'enrichissement qui suit.
    assert lead.contact["site_web"] == "https://studiocrea.co"
    assert lead.donnees_brutes["emails_connus"]["personal"] == 12
    # Discover ne rend aucun effectif, même quand on filtre dessus.
    assert lead.taille_effectif is None


def test_discover_accepte_les_deux_enveloppes_de_reponse(faux_http):
    """Hunter range tantôt les résultats dans `data`, tantôt sous `data.organizations`."""
    faux_http("hunter", charge={"data": {"organizations": [{"organization": "Agence Nova"}]}})
    leads = asyncio.run(
        HunterDiscover("cle").executer(
            _bloc_discover(industries_par_secteur={"design": ["Design Services"]}), limite=10
        )
    )
    assert [lead.nom for lead in leads] == ["Agence Nova"]


def test_discover_sans_critere_n_appelle_pas(faux_http):
    """Un Discover sans industrie balaierait toute la base de Hunter."""
    faux_http("hunter", exception=httpx.ConnectError("ne doit jamais être appelé"))
    assert asyncio.run(HunterDiscover("cle").executer(_bloc_discover(), limite=10)) == []
    assert HunterDiscover("cle").apercu(_bloc_discover(), 10) == []


def test_discover_enveloppe_les_filtres_et_n_envoie_pas_de_pagination():
    """`limit`/`offset` sont réservés au plan Premium : les envoyer casse l'appel.
    Et un filtre non enveloppé dans `include` est rejeté (400)."""
    bloc = _bloc_discover(
        industries_par_secteur={"marketing": ["Marketing Services"]},
        headcount=["1-10", "11-50"],
        headquarters_location=[{"country": "FR"}],
    )
    corps = HunterDiscover("cle").apercu(bloc, 25)[0].corps
    assert corps == {
        "industry": {"include": ["Marketing Services"]},
        "headcount": ["1-10", "11-50"],
        "headquarters_location": {"include": [{"country": "FR"}]},
    }
    assert "limit" not in corps and "offset" not in corps


def test_discover_interroge_un_secteur_a_la_fois():
    bloc = _bloc_discover(
        industries_par_secteur={"marketing": ["Marketing Services"], "design": ["Design Services"]}
    )
    requetes = HunterDiscover("cle").apercu(bloc, 25)
    assert [r.corps["industry"]["include"] for r in requetes] == [
        ["Marketing Services"],
        ["Design Services"],
    ]


def test_discover_dedoublonne_entre_secteurs(faux_http):
    """Une même entreprise peut ressortir sur deux industries."""
    faux_http("hunter", charge=CHARGE_DISCOVER)
    leads = asyncio.run(
        HunterDiscover("cle").executer(
            _bloc_discover(
                industries_par_secteur={
                    "marketing": ["Marketing Services"],
                    "communication": ["Public Relations and Communications Services"],
                }
            ),
            limite=10,
        )
    )
    assert [lead.nom for lead in leads] == ["Studio Créa"]


# ----- Hunter Domain Search : enrichissement --------------------------------
def _bloc_hunter(**filtres):
    return BlocRecherche(source="hunter", type="domain_search", filtres=filtres)


CHARGE_EMAILS = {
    "data": {
        "emails": [
            {
                "value": "support@studiocrea.co",
                "position": "Support Agent",
                "seniority": "junior",
                "confidence": 99,
            },
            {
                "value": "marie@studiocrea.co",
                "first_name": "Marie",
                "last_name": "Dupont",
                "position": "Founder",
                "seniority": "executive",
                "confidence": 88,
            },
        ]
    }
}


def _lead_a_enrichir(site="https://www.studiocrea.co/contact", **kw):
    return Lead(nom="Studio Créa", contact={"site_web": site}, **kw)


def test_hunter_choisit_le_decideur_pas_le_plus_confiant(faux_http):
    """L'email support a un meilleur indice : c'est le rôle visé qui prime."""
    faux_http("hunter", charge=CHARGE_EMAILS)
    lead = _lead_a_enrichir()

    nb = asyncio.run(
        Hunter("cle").enrichir([lead], _bloc_hunter(seniority=["executive"]), limite=10)
    )
    assert nb == 1
    assert lead.contact["email"] == "marie@studiocrea.co"
    assert lead.contact["nom"] == "Marie Dupont"
    assert lead.role_contact == "fondateur"
    assert lead.donnees_brutes["confiance_email"] == 88
    assert "hunter" in lead.donnees_brutes["sources"]


def test_hunter_sans_niveau_demande_retombe_sur_la_confiance(faux_http):
    faux_http("hunter", charge=CHARGE_EMAILS)
    lead = _lead_a_enrichir()

    asyncio.run(Hunter("cle").enrichir([lead], _bloc_hunter(), limite=10))
    assert lead.contact["email"] == "support@studiocrea.co"


def test_hunter_n_ecrase_pas_un_role_deja_identifie(faux_http):
    faux_http("hunter", charge=CHARGE_EMAILS)
    lead = _lead_a_enrichir(role_contact="directeur")

    asyncio.run(Hunter("cle").enrichir([lead], _bloc_hunter(), limite=10))
    assert lead.role_contact == "directeur"


def test_hunter_ignore_les_leads_sans_domaine_ou_deja_pourvus(faux_http):
    """Chaque appel évité est un crédit Hunter conservé."""
    faux_http("hunter", exception=httpx.ConnectError("ne doit jamais être appelé"))
    sans_site = Lead(nom="Inconnue")
    deja = Lead(nom="Nova", contact={"site_web": "nova.co", "email": "a@nova.co"})

    nb = asyncio.run(Hunter("cle").enrichir([sans_site, deja], _bloc_hunter(), limite=10))
    assert nb == 0


def test_hunter_sans_email_trouve_ne_compte_pas(faux_http):
    faux_http("hunter", charge={"data": {"emails": []}})
    lead = _lead_a_enrichir()

    nb = asyncio.run(Hunter("cle").enrichir([lead], _bloc_hunter(), limite=10))
    assert nb == 0
    assert "email" not in lead.contact


def test_hunter_dry_run_montre_un_gabarit_pas_un_domaine_invente():
    requetes = Hunter("cle").apercu(_bloc_hunter(seniority=["executive"]), 10)
    assert requetes[0].params["domain"] == "{domaine-du-lead}"
    assert requetes[0].params["seniority"] == "executive"


# ----- Extraction de domaine -------------------------------------------------
@pytest.mark.parametrize(
    "site, attendu",
    [
        ("https://www.studiocrea.co/fr/contact", "studiocrea.co"),
        ("studiocrea.co", "studiocrea.co"),
        ("http://Studio-Crea.CO:8080", "studio-crea.co"),
        ("", None),
    ],
)
def test_domaine_de(site, attendu):
    assert domaine_de(Lead(nom="A", contact={"site_web": site})) == attendu


def test_domaine_de_sans_contact():
    assert domaine_de(Lead(nom="A")) is None


# ----- Enchaînement découverte → enrichissement ------------------------------
def test_l_enrichissement_ne_touche_que_les_leads_retenus(faux_http):
    """Bout en bout : découverte → filtrage ICP → enrichissement.

    Le lead hors ICP est écarté AVANT l'enrichissement. Un seul lead est donc
    enrichi sur les deux découverts : les crédits Hunter ne partent pas sur des
    leads déjà jetés.
    """
    charge_apollo = {
        "people": [
            {
                "title": "Founder",
                "organization": {
                    "name": "Agence Nova",
                    "industry": "marketing",
                    "website_url": "https://nova.co",
                },
            },
            {
                "title": "Founder",
                "organization": {
                    "name": "Hôtel du Port",
                    "industry": "hotellerie",
                    "website_url": "https://hotel.co",
                },
            },
        ]
    }
    faux_http(
        routes={"apollo.io": charge_apollo, "domain-search": CHARGE_EMAILS},
        source=("apollo", "hunter"),
    )

    res = _executer(sources=["apollo", "hunter"], dry_run=False)

    assert [lead.nom for lead in res.leads] == ["Agence Nova"]
    assert res.rejetes_hors_icp == 1

    hunter = next(r for r in res.par_source if r.source == "hunter")
    assert hunter.statut == "ok"
    assert hunter.nb_enrichis == 1  # et non 2 : l'hôtel n'a jamais été interrogé
    # Un seul appel décrit, celui du lead retenu.
    assert [r.params["domain"] for r in hunter.requetes] == ["nova.co"]

    # Le lead enrichi devient exploitable : secteur, rôle ET email.
    nova = res.leads[0]
    assert nova.secteur == "marketing"
    assert nova.contact["email"] == "marie@studiocrea.co"
    assert nova.role_contact == "fondateur"


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
