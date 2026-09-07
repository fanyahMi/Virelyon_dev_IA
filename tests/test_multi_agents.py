"""Plusieurs agents par client — habilitation, isolation, et effet réel de la config.

Ce que ces tests protègent : qu'un agent ne puisse pas faire le travail d'un
autre, ni agir pour un autre client, et que le fait de le nommer **change
réellement** ce qu'il produit. Sans ce dernier point, tout le reste ne serait
qu'un contrôle d'accès sur une différence inexistante.
"""
from app.builder.config import charger_workspace, identifiant_agent
from app.builder.roles import CAPACITES, CAPACITES_PAR_ROLE
from tests.conftest import HEADERS

TEST_ARES = "11111111-1111-1111-1111-111111111111"
RESTAURATION = "096c84c1-cf60-4c2b-ab87-8132570a59f4"
INCONNU = "99999999-9999-9999-9999-999999999999"

PROSPECTEUR = str(identifiant_agent(TEST_ARES, "prospecteur"))
RELANCEUR = str(identifiant_agent(TEST_ARES, "relanceur"))

LEAD = {"nom": "Studio Créa", "secteur": "marketing"}
ICP = {"secteurs_inclus": ["marketing"]}


# ----- Le registre des rôles -------------------------------------------------
def test_les_deux_roles_ne_se_recouvrent_pas():
    """S'ils avaient les mêmes capacités, il n'y aurait qu'un seul agent."""
    prospecteur = CAPACITES_PAR_ROLE["prospecteur"]
    relanceur = CAPACITES_PAR_ROLE["relanceur"]
    assert prospecteur != relanceur
    assert "sourcing" in prospecteur and "sourcing" not in relanceur
    assert "classify" in relanceur and "classify" not in prospecteur
    assert "decide" in relanceur and "decide" not in prospecteur


def test_toute_capacite_declaree_est_tenue_par_au_moins_un_role():
    """Une capacité que personne ne porte est un endpoint devenu inatteignable."""
    couvertes = set().union(*CAPACITES_PAR_ROLE.values())
    assert set(CAPACITES) == couvertes


# ----- Habilitation : chacun son poste ---------------------------------------
def test_le_prospecteur_ne_peut_pas_classer_une_reponse(client, use_provider):
    use_provider('{"categorie": "Intéressé", "confiance": 0.9, "date_relance": null}')
    r = client.post(
        "/api/v1/ares/classify",
        headers=HEADERS,
        json={"workspace_id": TEST_ARES, "agent_id": PROSPECTEUR,
              "message_entrant": "Oui, ça m'intéresse !"},
    )
    assert r.status_code == 403
    assert "classify" in r.json()["detail"]


def test_le_relanceur_ne_peut_pas_aller_prospecter(client):
    r = client.post(
        "/api/v1/sourcing/executer",
        headers=HEADERS,
        json={"workspace_id": TEST_ARES, "agent_id": RELANCEUR,
              "icp": {"secteurs_inclus": ["marketing"]}},
    )
    assert r.status_code == 403
    assert "sourcing" in r.json()["detail"]


def test_chacun_dans_son_poste_est_accepte(client, use_provider):
    use_provider('{"categorie": "Intéressé", "confiance": 0.9, "date_relance": null}')
    r = client.post(
        "/api/v1/ares/classify",
        headers=HEADERS,
        json={"workspace_id": TEST_ARES, "agent_id": RELANCEUR,
              "message_entrant": "Oui, ça m'intéresse !"},
    )
    assert r.status_code == 200


def test_un_agent_inconnu_est_refuse(client, use_provider):
    use_provider('{"qualifie": true, "confiance": 0.9, "motif": "ok"}')
    r = client.post(
        "/api/v1/ares/qualify",
        headers=HEADERS,
        json={"workspace_id": TEST_ARES, "agent_id": INCONNU, "lead": LEAD, "icp": ICP},
    )
    assert r.status_code == 404


def test_un_agent_non_active_ne_travaille_pas(client, use_provider):
    """Le relanceur de ce client n'est pas encore activé : refus explicite."""
    use_provider('{"texte": "Bonjour", "canal": "email"}')
    r = client.post(
        "/api/v1/ares/generate",
        headers=HEADERS,
        json={"workspace_id": RESTAURATION,
              "agent_id": str(identifiant_agent(RESTAURATION, "relanceur")),
              "lead": LEAD},
    )
    assert r.status_code == 409
    assert "inactif" in r.json()["detail"]


def test_lagent_dun_autre_client_est_introuvable(client, use_provider):
    """Le risque n°1 du projet : agir avec l'agent d'un autre client."""
    use_provider('{"qualifie": true, "confiance": 0.9, "motif": "ok"}')
    r = client.post(
        "/api/v1/ares/qualify",
        headers=HEADERS,
        json={"workspace_id": TEST_ARES,
              "agent_id": str(identifiant_agent(RESTAURATION, "prospecteur")),
              "lead": LEAD, "icp": ICP},
    )
    assert r.status_code == 404


def test_sans_agent_nomme_le_comportement_dorigine_est_conserve(client, use_provider):
    """Rétro-compatibilité : le backend n'a pas encore migré."""
    use_provider('{"categorie": "Intéressé", "confiance": 0.9, "date_relance": null}')
    r = client.post(
        "/api/v1/ares/classify",
        headers=HEADERS,
        json={"workspace_id": TEST_ARES, "message_entrant": "Oui !"},
    )
    assert r.status_code == 200


# ----- L'agent change réellement le résultat ---------------------------------
def test_deux_agents_notent_le_meme_lead_differemment(client, score_payload):
    """Même lead, même ICP, deux agents : deux notes. C'est tout l'intérêt."""
    prospecteur = client.post(
        "/api/v1/ares/score", headers=HEADERS,
        json={**score_payload, "agent_id": PROSPECTEUR},
    ).json()
    relanceur = client.post(
        "/api/v1/ares/score", headers=HEADERS,
        json={**score_payload, "agent_id": RELANCEUR},
    ).json()

    # Le relanceur pondère l'engagement à 0.40 ; ce lead n'a aucun signal.
    assert prospecteur["score"] > relanceur["score"]
    assert prospecteur["breakdown"]["poids"] != relanceur["breakdown"]["poids"]


def test_le_scoring_config_du_corps_est_ignore_quand_un_agent_est_nomme(
    client, score_payload
):
    """La configuration de l'agent l'emporte — sinon l'appelant peut la contourner."""
    detourne = {
        **score_payload,
        "agent_id": RELANCEUR,
        "scoring_config": {"poids_fraicheur": 1.0, "poids_completude": 0.0,
                           "poids_fit": 0.0, "poids_engagement": 0.0},
    }
    poids = client.post("/api/v1/ares/score", headers=HEADERS, json=detourne).json()
    assert poids["breakdown"]["poids"]["poids_engagement"] == 0.40


def test_le_canal_est_contraint_aux_canaux_de_lagent(client, use_provider):
    """Le prospecteur n'a que l'email : un canal inventé est rabattu dessus."""
    use_provider('{"texte": "Bonjour", "canal": "sms"}')
    r = client.post(
        "/api/v1/ares/generate",
        headers=HEADERS,
        json={"workspace_id": TEST_ARES, "agent_id": PROSPECTEUR, "lead": LEAD},
    )
    assert r.json()["canal"] == "email"


def test_la_mission_de_lagent_atteint_le_prompt(client, use_provider, monkeypatch):
    """Sans elle, les deux agents produiraient des messages interchangeables."""
    vus: list[str] = []

    class Espion:
        async def generate(self, model, system, user, max_tokens):
            vus.append(user)
            return '{"texte": "Bonjour", "canal": "email"}', 10, 5

    from app.gateway.provider import get_provider
    from app.main import app

    app.dependency_overrides[get_provider] = lambda: Espion()
    try:
        for agent_id in (PROSPECTEUR, RELANCEUR):
            client.post(
                "/api/v1/ares/generate", headers=HEADERS,
                json={"workspace_id": TEST_ARES, "agent_id": agent_id, "lead": LEAD},
            )
    finally:
        app.dependency_overrides.pop(get_provider, None)

    attendu = charger_workspace(TEST_ARES)
    assert attendu.par_role("prospecteur").objectif_principal in vus[0]
    assert attendu.par_role("relanceur").objectif_principal in vus[1]
    assert vus[0] != vus[1]


# ----- Coûts : qui a dépensé quoi -------------------------------------------
def test_les_couts_sont_attribues_a_chaque_agent(client, use_provider):
    from app.gateway.cost_tracker import cost_tracker

    cost_tracker.clear()
    use_provider('{"qualifie": true, "confiance": 0.9, "motif": "ok"}')
    client.post(
        "/api/v1/ares/qualify", headers=HEADERS,
        json={"workspace_id": TEST_ARES, "agent_id": PROSPECTEUR,
              "lead": LEAD, "icp": ICP},
    )

    couts = client.get(f"/api/v1/costs/{TEST_ARES}", headers=HEADERS).json()
    assert couts["cost"] > 0
    assert couts["par_agent"][PROSPECTEUR]["cost"] == couts["cost"]
    assert RELANCEUR not in couts["par_agent"]  # il n'a rien dépensé


def test_les_appels_sans_agent_ne_sont_imputes_a_personne(client, use_provider):
    from app.gateway.cost_tracker import cost_tracker

    cost_tracker.clear()
    use_provider('{"qualifie": true, "confiance": 0.9, "motif": "ok"}')
    client.post(
        "/api/v1/ares/qualify", headers=HEADERS,
        json={"workspace_id": TEST_ARES, "lead": LEAD, "icp": ICP},
    )

    couts = client.get(f"/api/v1/costs/{TEST_ARES}", headers=HEADERS).json()
    assert couts["par_agent"]["-"]["cost"] == couts["cost"]


def test_deux_agents_ne_partagent_pas_le_cache(client, use_provider):
    """Sinon le second se verrait offrir ce que le premier a payé."""
    from app.gateway.cost_tracker import cost_tracker

    cost_tracker.clear()
    use_provider('{"categorie": "Intéressé", "confiance": 0.9, "date_relance": null}')
    corps = {"workspace_id": TEST_ARES, "message_entrant": "Oui !"}

    a = client.post("/api/v1/ares/classify", headers=HEADERS,
                    json={**corps, "agent_id": RELANCEUR}).json()
    b = client.post("/api/v1/ares/classify", headers=HEADERS, json=corps).json()

    assert a["meta"]["cached"] is False
    assert b["meta"]["cached"] is False  # clé distincte : pas de resquille


# ----- Surface HTTP ----------------------------------------------------------
def test_lendpoint_de_config_rend_lequipe(client):
    r = client.get(f"/api/v1/builder/config/{TEST_ARES}", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert [a["role"] for a in body["agents"]] == ["prospecteur", "relanceur"]
    assert body["icp"]["taille_max"] == 30  # l'ICP est au niveau du workspace


def test_lendpoint_dun_agent_precis(client):
    r = client.get(
        f"/api/v1/builder/config/{TEST_ARES}/agents/{RELANCEUR}", headers=HEADERS
    )
    assert r.status_code == 200
    assert r.json()["role"] == "relanceur"


def test_lendpoint_dun_agent_etranger_rend_404(client):
    etranger = identifiant_agent(RESTAURATION, "prospecteur")
    r = client.get(
        f"/api/v1/builder/config/{TEST_ARES}/agents/{etranger}", headers=HEADERS
    )
    assert r.status_code == 404
