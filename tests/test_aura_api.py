"""Endpoints AURA avec un provider mocké (aucun appel Claude réel)."""
import json
import uuid

import pytest

from app.gateway.provider import (
    LLMIndisponible,
    LLMNotConfiguredError,
    LLMSurcharge,
    get_provider,
)
from app.main import app
from tests.conftest import HEADERS, FakeProvider

WID = "11111111-1111-1111-1111-111111111111"
PERIODE = {"debut": "2026-09-01", "fin": "2026-09-30"}
URL_N, URL_P, URL_D = (f"/api/v1/aura/{p}" for p in ("narrative", "performance", "indicator-availability"))

CA = {"id": "ca", "libelle": "Chiffre d'affaires", "valeur": 15000, "unite": "EUR"}
MANQUANT = {"id": "marge", "libelle": "Marge", "motif": "source de facturation non connectée"}
NARRATION = {"resume": "CA de 15000 EUR.", "analyses": [
    {"indicateur_id": "ca", "commentaire": "15000 EUR.", "tendance": "indeterminee"}]}


class Enregistreur(FakeProvider):
    """Garde ce qui est envoyé au LLM, compte les appels."""

    def __init__(self, payload) -> None:
        super().__init__(payload if isinstance(payload, str) else json.dumps(payload))
        self.appels: list[str] = []

    async def generate(self, model, system, user, max_tokens):
        self.appels.append(user)
        return await super().generate(model, system, user, max_tokens)


@pytest.fixture
def provider():
    """Installe un provider ; rend la fonction qui le crée."""
    def _set(p):
        app.dependency_overrides[get_provider] = lambda: p
        return p

    yield _set
    app.dependency_overrides.pop(get_provider, None)


def narr(**kw):
    return {"workspace_id": WID, "indicateurs_calcules": [CA], **kw}


# ----- Requête / réponse valides -----
def test_narrative_valide(client, provider):
    p = provider(Enregistreur(NARRATION))
    r = client.post(URL_N, headers=HEADERS, json=narr(objectif_principal="Suivre le CA", locale="fr"))
    assert r.status_code == 200
    body = r.json()
    assert body["resume"] == "CA de 15000 EUR."
    assert body["analyses"][0]["indicateur_id"] == "ca"
    assert body["meta"]["model_used"] == "claude-sonnet-4-6" and body["meta"]["cost_estimate"] > 0
    assert len(p.appels) == 1


def test_performance_valide(client, provider):
    provider(Enregistreur({"resume": "ok", "ares": {"synthese": "120 leads", "metriques_citees": ["leads_generes"]},
                           "apex": {"synthese": "75 conv", "metriques_citees": ["conversations"]}}))
    r = client.post(URL_P, headers=HEADERS, json={
        "workspace_id": WID, "periode": PERIODE, "locale": "fr",
        "ares": {"leads_generes": 120, "leads_qualifies": 38},
        "apex": {"conversations": 75, "tickets_resolus": 61}})
    assert r.status_code == 200
    assert r.json()["ares"]["metriques_citees"] == ["leads_generes"]


def test_disponibilite_valide(client, provider):
    provider(Enregistreur({"disponible": False, "motif": "source de facturation non connectée"}))
    r = client.post(URL_D, headers=HEADERS, json={
        "workspace_id": WID, "indicateur_id": "ca",
        "sources_requises": [{"nom": "facturation", "connectee": False, "champs_requis": ["montant"]}]})
    assert r.status_code == 200
    assert r.json()["disponible"] is False
    assert r.json()["motif"] == "source de facturation non connectée"
    assert r.json()["meta"]["model_used"] == "claude-haiku-4-5"


# ----- Requêtes invalides → 422, aucun appel LLM -----
@pytest.mark.parametrize("corps", [
    {"indicateurs_calcules": [CA]},                                            # workspace_id absent
    {"workspace_id": "pas-un-uuid", "indicateurs_calcules": [CA]},
    narr(locale="francais"),
    narr(champ_inconnu="x"),                                                   # champ hors contrat : refusé
    narr(indicateurs_calcules=[{**CA, "valeur": None}]),                       # null ≠ 0
    narr(indicateurs_calcules=[{**CA, "valeur": "15000"}]),
    narr(indicateurs_calcules=[{"id": "ca", "libelle": "CA"}]),                # valeur absente
    narr(donnees_manquantes=[{"id": "ca", "libelle": "CA", "motif": "x"}]),    # même id calculé + manquant
])
def test_narrative_requete_invalide(client, provider, corps):
    p = provider(Enregistreur(NARRATION))
    assert client.post(URL_N, headers=HEADERS, json=corps).status_code == 422
    assert p.appels == []


def test_performance_et_disponibilite_requete_invalide(client, provider):
    p = provider(Enregistreur({}))
    base = {"workspace_id": WID, "periode": PERIODE}
    assert client.post(URL_P, headers=HEADERS, json=base).status_code == 422                       # aucun agent
    assert client.post(URL_P, headers=HEADERS, json={**base, "ares": {"a": None}}).status_code == 422
    assert client.post(URL_P, headers=HEADERS, json={**base, "ares": {"a": 1},
                       "periode": {"debut": "2026-10-01", "fin": "2026-09-01"}}).status_code == 422
    assert client.post(URL_D, headers=HEADERS, json={
        "workspace_id": WID, "indicateur_id": "ca", "sources_requises": []}).status_code == 422
    assert p.appels == []


def test_aucun_indicateur_422_sans_appel_llm(client, provider):
    p = provider(Enregistreur(NARRATION))
    r = client.post(URL_N, headers=HEADERS, json={"workspace_id": WID})
    assert r.status_code == 422 and "aucun indicateur" in r.json()["detail"]
    assert p.appels == []


@pytest.mark.parametrize("url", [URL_N, URL_P, URL_D])
def test_endpoints_proteges(client, url):
    assert client.post(url, json={}).status_code == 401
    assert client.post(url, json={}, headers={"X-Internal-Key": "mauvaise"}).status_code == 401


# ----- Réponse Claude invalide → erreur technique, jamais une réponse vide -----
@pytest.mark.parametrize("brut", [
    "ceci n'est pas du JSON",
    '{"analyses": []}',                                     # résumé absent
    '{"resume": "x", "inconnu": 1}',                        # champ en trop
    '{"resume": "x", "analyses": [{"indicateur_id": "inventé", "commentaire": "c"}]}',
])
def test_narrative_reponse_claude_invalide_502(client, provider, brut):
    provider(Enregistreur(brut))
    r = client.post(URL_N, headers=HEADERS, json=narr())
    assert r.status_code == 502
    assert "resume" not in r.json()  # pas de contenu de substitution


def test_performance_et_disponibilite_reponse_invalide_502(client, provider):
    provider(Enregistreur('{"resume": "x"}'))
    assert client.post(URL_P, headers=HEADERS, json={
        "workspace_id": WID, "periode": PERIODE, "ares": {"a": 1}}).status_code == 502
    provider(Enregistreur('{"disponible": true, "motif": "incohérent"}'))
    assert client.post(URL_D, headers=HEADERS, json={
        "workspace_id": WID, "indicateur_id": "ca",
        "sources_requises": [{"nom": "f", "connectee": True}]}).status_code == 502


def test_reponse_invalide_non_gardee_en_cache_entre_deux_appels(client, provider):
    p = provider(Enregistreur('{"resume": "x", "analyses": [{"indicateur_id": "zzz", "commentaire": "c"}]}'))
    assert client.post(URL_N, headers=HEADERS, json=narr()).status_code == 502
    p.payload = json.dumps(NARRATION)
    assert client.post(URL_N, headers=HEADERS, json=narr()).status_code == 200
    assert len(p.appels) == 2


# ----- Erreurs provider : conventions du Gateway -----
@pytest.mark.parametrize("exc, statut", [
    (LLMSurcharge("429"), 429),
    (LLMIndisponible("HTTP 500"), 503),
    (LLMNotConfiguredError("pas de clé"), 503),
])
def test_erreurs_provider(client, provider, exc, statut):
    class EnPanne(FakeProvider):
        async def generate(self, *a, **k):
            raise exc

    provider(EnPanne("{}"))
    for url, corps in ((URL_N, narr()),
                       (URL_P, {"workspace_id": WID, "periode": PERIODE, "ares": {"a": 1}}),
                       (URL_D, {"workspace_id": WID, "indicateur_id": "ca",
                                "sources_requises": [{"nom": "f", "connectee": True}]})):
        r = client.post(url, headers=HEADERS, json=corps)
        assert r.status_code == statut and "detail" in r.json()


def test_plafond_de_cout_429(client, provider, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setenv("MAX_COST_PER_WORKSPACE", "0.0000001")
    get_settings.cache_clear()
    try:
        provider(Enregistreur(NARRATION))
        neuf = narr(workspace_id=str(uuid.uuid4()))  # le suivi des coûts est global : workspace inédit
        assert client.post(URL_N, headers=HEADERS, json=neuf).status_code == 200
        assert client.post(URL_N, headers=HEADERS, json={**neuf, "objectif_principal": "autre"}).status_code == 429
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


# ----- Données manquantes et zéro : ce qui part vraiment vers le LLM -----
def test_donnee_manquante_jamais_transformee_en_valeur(client, provider):
    p = provider(Enregistreur({**NARRATION, "donnees_manquantes": [MANQUANT]}))
    r = client.post(URL_N, headers=HEADERS, json=narr(donnees_manquantes=[MANQUANT]))
    assert r.status_code == 200
    assert r.json()["donnees_manquantes"] == [MANQUANT]
    envoye = json.loads(p.appels[0])
    assert envoye["donnees_manquantes"][0]["statut"] == "donnee_manquante"
    assert "valeur" not in envoye["donnees_manquantes"][0]
    assert all(i["id"] != "marge" for i in envoye["indicateurs_calcules"])


def test_donnee_manquante_masquee_par_claude_502(client, provider):
    provider(Enregistreur({**NARRATION, "donnees_manquantes": []}))
    assert client.post(URL_N, headers=HEADERS, json=narr(donnees_manquantes=[MANQUANT])).status_code == 502


def test_valeur_zero_conservee_de_bout_en_bout(client, provider):
    p = provider(Enregistreur({"resume": "Aucun lead.", "analyses": [
        {"indicateur_id": "leads", "commentaire": "0 lead", "tendance": "stable"}]}))
    r = client.post(URL_N, headers=HEADERS, json=narr(indicateurs_calcules=[
        {"id": "leads", "libelle": "Leads", "valeur": 0, "valeur_precedente": 0}]))
    assert r.status_code == 200
    envoye = json.loads(p.appels[0])["indicateurs_calcules"][0]
    assert envoye["valeur"] == 0 and envoye["valeur_precedente"] == 0 and envoye["statut"] == "calcule"

    q = provider(Enregistreur({"resume": "r", "ares": {"synthese": "s"}}))
    client.post(URL_P, headers=HEADERS, json={"workspace_id": WID, "periode": PERIODE, "ares": {
        "leads_generes": 0, "leads_qualifies": {"donnee_manquante": True, "motif": "CRM non connecté"}}})
    ares = json.loads(q.appels[0])["ares"]
    assert ares["leads_generes"] == 0
    assert ares["leads_qualifies"]["statut"] == "donnee_manquante"


# ----- Isolation du workspace -----
def test_workspace_id_jamais_envoye_au_llm(client, provider):
    p = provider(Enregistreur(NARRATION))
    client.post(URL_N, headers=HEADERS, json=narr())
    assert WID not in p.appels[0]


def test_deux_workspaces_ne_partagent_ni_cache_ni_couts(client, provider):
    p = provider(Enregistreur(NARRATION))
    ws1, ws2 = str(uuid.uuid4()), str(uuid.uuid4())  # le suivi des coûts est global : workspaces inédits
    corps = narr(workspace_id=ws1)
    assert client.post(URL_N, headers=HEADERS, json=corps).json()["meta"]["cached"] is False
    # même corps, même workspace → cache
    assert client.post(URL_N, headers=HEADERS, json=corps).json()["meta"]["cached"] is True
    # même corps, AUTRE workspace → pas de cache croisé, nouvel appel LLM
    autre = client.post(URL_N, headers=HEADERS, json={**corps, "workspace_id": ws2})
    assert autre.json()["meta"]["cached"] is False
    assert len(p.appels) == 2
    c1 = client.get(f"/api/v1/costs/{ws1}", headers=HEADERS).json()
    c2 = client.get(f"/api/v1/costs/{ws2}", headers=HEADERS).json()
    assert c1["input_tokens"] == 100 and c2["input_tokens"] == 100  # un seul appel facturé chacun
