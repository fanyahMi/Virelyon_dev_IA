"""Tests du plan de recherche : ICP → requêtes par source. Logique pure, aucun LLM."""
from app.builder.plan_recherche import construire_plan
from app.schemas.ares import ICP
from app.schemas.builder import PlanRechercheRequest
from tests.conftest import HEADERS

WID = "11111111-1111-1111-1111-111111111111"


def _plan(sources=None, zone=None, **icp_kwargs):
    return construire_plan(
        PlanRechercheRequest(
            workspace_id=WID, icp=ICP(**icp_kwargs), sources=sources or [], zone=zone
        )
    )


def _bloc(plan, source):
    for bloc in plan.decouverte + plan.enrichissement:
        if bloc.source == source:
            return bloc
    return None


# ----- Google Maps -----------------------------------------------------------
def test_maps_traduit_les_secteurs_en_requetes():
    plan = _plan(sources=["google_maps"], secteurs_inclus=["marketing", "conseil"])
    bloc = _bloc(plan, "google_maps")
    assert bloc.type == "requetes_texte"
    assert "agence marketing" in bloc.requetes
    assert "cabinet de conseil" in bloc.requetes


def test_maps_concatene_la_zone_quand_elle_est_fournie():
    plan = _plan(sources=["google_maps"], secteurs_inclus=["marketing"], zone="Lyon")
    assert all(r.endswith("Lyon") for r in _bloc(plan, "google_maps").requetes)


def test_maps_n_invente_jamais_de_zone():
    # Contrainte CDCF §8 : aucun ciblage géographique déduit.
    plan = _plan(sources=["google_maps"], secteurs_inclus=["marketing"])
    requetes = _bloc(plan, "google_maps").requetes
    assert requetes == ["agence marketing", "agence de publicité"]
    assert any(d.champ == "zone" for d in plan.diagnostics)


# ----- Apollo ----------------------------------------------------------------
def test_apollo_construit_des_filtres_structures():
    plan = _plan(
        sources=["apollo"],
        secteurs_inclus=["developpement"],
        taille_min=5,
        taille_max=30,
        roles_cibles=["fondateur"],
    )
    filtres = _bloc(plan, "apollo").filtres
    assert "information technology & services" in filtres["organization_industries"]
    assert filtres["organization_num_employees_ranges"] == ["5,30"]
    assert "Founder" in filtres["person_titles"]


def test_apollo_bornes_ouvertes():
    plan = _plan(sources=["apollo"], secteurs_inclus=["conseil"], taille_min=10)
    assert _bloc(plan, "apollo").filtres["organization_num_employees_ranges"] == ["10,10000"]


def test_apollo_sans_taille_n_envoie_pas_de_tranche():
    plan = _plan(sources=["apollo"], secteurs_inclus=["conseil"])
    assert "organization_num_employees_ranges" not in _bloc(plan, "apollo").filtres


def test_titres_bilingues_depuis_les_roles():
    plan = _plan(sources=["apollo"], secteurs_inclus=["rh"], roles_cibles=["decideur"])
    titres = _bloc(plan, "apollo").filtres["person_titles"]
    assert "CEO" in titres and "Gérant" in titres


# ----- LinkedIn : réserve juridique -----------------------------------------
def test_linkedin_porte_un_avertissement():
    plan = _plan(sources=["linkedin"], secteurs_inclus=["marketing"], roles_cibles=["fondateur"])
    bloc = _bloc(plan, "linkedin")
    assert bloc.avertissement is not None
    assert "conditions d'utilisation" in bloc.avertissement
    assert any("conditions d'utilisation" in d.message for d in plan.diagnostics)


# ----- Découverte vs enrichissement -----------------------------------------
def test_hunter_et_site_web_sont_de_l_enrichissement():
    plan = _plan(
        sources=["hunter", "site_web"], secteurs_inclus=["marketing"], roles_cibles=["fondateur"]
    )
    assert plan.decouverte == []
    assert {b.source for b in plan.enrichissement} == {"hunter", "site_web"}
    assert _bloc(plan, "site_web").champs_cibles == [
        "secteur",
        "description",
        "taille_estimee",
        "signaux",
    ]


def test_sans_sources_toutes_les_sources_sont_planifiees():
    plan = _plan(secteurs_inclus=["marketing"], roles_cibles=["fondateur"])
    assert {b.source for b in plan.decouverte} == {
        "google_maps",
        "apollo",
        "linkedin",
        "openstreetmap",
    }
    assert {b.source for b in plan.enrichissement} == {"site_web", "hunter"}


def test_source_inconnue_est_ignoree_avec_avertissement():
    plan = _plan(sources=["facebook"], secteurs_inclus=["marketing"])
    assert plan.decouverte == [] and plan.enrichissement == []
    assert any("facebook" in d.message for d in plan.diagnostics)


# ----- Exclusions ------------------------------------------------------------
def test_secteurs_exclus_remontent_pour_filtrage_posterieur():
    plan = _plan(
        sources=["apollo"], secteurs_inclus=["marketing"], secteurs_exclus=["hotellerie"]
    )
    assert plan.secteurs_exclus == ["hotellerie"]
    assert any("après collecte" in d.message for d in plan.diagnostics)


# ----- Erreur bloquante ------------------------------------------------------
def test_sans_secteur_le_sourcing_ne_peut_pas_demarrer():
    plan = _plan(sources=["google_maps"], roles_cibles=["fondateur"])
    assert any(
        d.niveau == "erreur" and d.champ == "icp.secteurs_inclus" for d in plan.diagnostics
    )


def test_secteur_sans_libelle_retombe_sur_la_saisie_client():
    # Secteur personnalisé : aucun libellé prédéfini, on utilise la saisie telle quelle.
    plan = _plan(sources=["google_maps"], secteurs_inclus=["plomberie industrielle"])
    assert _bloc(plan, "google_maps").requetes == ["plomberie industrielle"]
    assert any(
        d.niveau == "info" and "libellé de recherche" in d.message for d in plan.diagnostics
    )


def test_secteur_personnalise_est_canonise_dans_les_exclusions():
    plan = _plan(
        sources=["apollo"],
        secteurs_inclus=["marketing"],
        secteurs_exclus=["Plomberie industrielle"],
    )
    assert plan.secteurs_exclus == ["plomberie_industrielle"]


# ----- Déterminisme ----------------------------------------------------------
def test_plan_est_reproductible():
    kwargs = dict(
        sources=["google_maps", "apollo"],
        secteurs_inclus=["marketing", "conseil"],
        taille_min=5,
        taille_max=30,
        roles_cibles=["fondateur"],
        zone="Paris",
    )
    assert _plan(**kwargs).model_dump() == _plan(**kwargs).model_dump()


# ----- Endpoint --------------------------------------------------------------
def test_plan_recherche_endpoint(client):
    payload = {
        "workspace_id": WID,
        "icp": {
            "secteurs_inclus": ["marketing", "communication"],
            "secteurs_exclus": ["hotellerie"],
            "taille_min": 5,
            "taille_max": 30,
            "roles_cibles": ["fondateur", "decideur"],
        },
        "sources": ["google_maps", "apollo", "hunter"],
        "zone": "Genève",
    }
    r = client.post("/api/v1/builder/plan-recherche", json=payload, headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert {b["source"] for b in body["decouverte"]} == {"google_maps", "apollo"}
    assert body["enrichissement"][0]["source"] == "hunter"
    assert body["secteurs_exclus"] == ["hotellerie"]


def test_plan_recherche_exige_la_cle(client):
    r = client.post("/api/v1/builder/plan-recherche", json={})
    assert r.status_code == 401
