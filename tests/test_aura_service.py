"""Comportement IA d'AURA avec un provider mocké (aucun appel Claude réel)."""
import asyncio
import inspect
import json

import pytest

from app.aura import service
from app.aura.schemas import (
    IndicatorAvailabilityInput,
    NarrativeInput,
    PerformanceInput,
)
from app.gateway.provider import ReponseLLMInvalide
from app.gateway.router import Gateway
from app.prompts.aura import AVAILABILITY_SYSTEM, NARRATIVE_SYSTEM, PERFORMANCE_SYSTEM
from tests.conftest import FakeProvider

WID = "11111111-1111-1111-1111-111111111111"
PERIODE = {"debut": "2026-09-01", "fin": "2026-09-30"}


class EnregistreurProvider(FakeProvider):
    """FakeProvider qui garde ce que le service a envoyé au LLM."""

    def __init__(self, *payloads: str) -> None:
        super().__init__(payloads[0])
        self.payloads = list(payloads)
        self.appels: list[dict] = []

    async def generate(self, model, system, user, max_tokens):
        self.appels.append({"model": model, "system": system, "user": user, "max_tokens": max_tokens})
        i = min(len(self.appels) - 1, len(self.payloads) - 1)
        return self.payloads[i], 100, 50


def lancer(coro_fn, provider, req):
    return asyncio.run(coro_fn(Gateway(provider), req))


def _narr(**kw) -> NarrativeInput:
    return NarrativeInput(workspace_id=WID, **kw)


CA = {"id": "ca", "libelle": "Chiffre d'affaires", "valeur": 15000, "unite": "EUR"}
MARGE_MANQUANTE = {"id": "marge", "libelle": "Marge", "motif": "source de facturation non connectée"}
NARRATION_OK = json.dumps({
    "resume": "Le CA atteint 15000 EUR.",
    "analyses": [{"indicateur_id": "ca", "commentaire": "15000 EUR.", "tendance": "indeterminee"}],
    "points_attention": [],
    "donnees_manquantes": [MARGE_MANQUANTE],
})


# ----- Narration -----
def test_narration_valide_via_le_gateway_existant():
    p = EnregistreurProvider(NARRATION_OK)
    r = lancer(service.generate_narrative, p, _narr(indicateurs_calcules=[CA], donnees_manquantes=[MARGE_MANQUANTE]))
    assert r.resume.startswith("Le CA")
    assert r.analyses[0].indicateur_id == "ca"
    assert r.meta.model_used == "claude-sonnet-4-6"  # tier reasoning
    assert r.meta.cost_estimate > 0
    assert p.appels[0]["system"] == NARRATIVE_SYSTEM


def test_donnee_manquante_transmise_avec_statut_explicite_jamais_zero():
    p = EnregistreurProvider(NARRATION_OK)
    lancer(service.generate_narrative, p, _narr(indicateurs_calcules=[CA], donnees_manquantes=[MARGE_MANQUANTE]))
    envoye = json.loads(p.appels[0]["user"])
    assert envoye["donnees_manquantes"] == [
        {"id": "marge", "libelle": "Marge", "statut": "donnee_manquante",
         "motif": "source de facturation non connectée"}
    ]
    assert all(i["id"] != "marge" for i in envoye["indicateurs_calcules"])
    assert "valeur" not in envoye["donnees_manquantes"][0]


def test_donnee_manquante_renvoyee_est_celle_de_l_entree():
    reponse = json.loads(NARRATION_OK)
    reponse["donnees_manquantes"] = [{**MARGE_MANQUANTE, "motif": "motif reformulé par le modèle"}]
    p = EnregistreurProvider(json.dumps(reponse))
    r = lancer(service.generate_narrative, p, _narr(indicateurs_calcules=[CA], donnees_manquantes=[MARGE_MANQUANTE]))
    assert r.donnees_manquantes[0].motif == "source de facturation non connectée"


def test_donnee_manquante_masquee_par_le_modele_est_refusee():
    reponse = json.loads(NARRATION_OK)
    reponse["donnees_manquantes"] = []
    p = EnregistreurProvider(json.dumps(reponse))
    with pytest.raises(ReponseLLMInvalide):
        lancer(service.generate_narrative, p, _narr(indicateurs_calcules=[CA], donnees_manquantes=[MARGE_MANQUANTE]))


def test_valeur_zero_envoyee_comme_vraie_valeur():
    zero = {"id": "leads", "libelle": "Leads", "valeur": 0, "valeur_precedente": 0}
    p = EnregistreurProvider(json.dumps({
        "resume": "Aucun lead.",
        "analyses": [{"indicateur_id": "leads", "commentaire": "0 lead.", "tendance": "stable"}],
    }))
    r = lancer(service.generate_narrative, p, _narr(indicateurs_calcules=[zero]))
    envoye = json.loads(p.appels[0]["user"])["indicateurs_calcules"][0]
    assert envoye["valeur"] == 0 and envoye["statut"] == "calcule"
    assert envoye["valeur_precedente"] == 0
    assert r.analyses[0].tendance == "stable"


def test_absence_totale_de_kpi_aucun_appel_llm():
    class Interdit(FakeProvider):
        async def generate(self, *a, **k):
            raise AssertionError("le LLM ne doit pas être appelé")

    with pytest.raises(service.DonneesInsuffisantes):
        lancer(service.generate_narrative, Interdit("{}"), _narr())


def test_narration_json_invalide():
    with pytest.raises(ReponseLLMInvalide):
        lancer(service.generate_narrative, FakeProvider("pas du JSON"), _narr(indicateurs_calcules=[CA]))


@pytest.mark.parametrize("reponse", [
    {"analyses": []},                                                    # résumé absent
    {"resume": "x", "analyses": [], "inconnu": 1},                       # champ en trop
    {"resume": "x", "analyses": [{"indicateur_id": "ca", "commentaire": "c", "tendance": "explose"}]},
    {"resume": "x", "analyses": [{"indicateur_id": "ca", "commentaire": "c", "tendance": "indeterminee"}],
     "points_attention": "pas une liste"},
])
def test_narration_sortie_pydantic_invalide(reponse):
    with pytest.raises(ReponseLLMInvalide):
        lancer(service.generate_narrative, FakeProvider(json.dumps(reponse)), _narr(indicateurs_calcules=[CA]))


def test_analyse_d_un_indicateur_inexistant_ou_manquant_refusee():
    for ident in ("inventé", "marge"):
        rep = {"resume": "x", "analyses": [{"indicateur_id": ident, "commentaire": "c"}],
               "donnees_manquantes": [MARGE_MANQUANTE]}
        with pytest.raises(ReponseLLMInvalide):
            lancer(service.generate_narrative, FakeProvider(json.dumps(rep)),
                   _narr(indicateurs_calcules=[CA], donnees_manquantes=[MARGE_MANQUANTE]))


def test_tendance_non_demontrable_refusee():
    # aucune valeur précédente → « hausse » n'est pas démontrée
    rep = {"resume": "x", "analyses": [{"indicateur_id": "ca", "commentaire": "c", "tendance": "hausse"}]}
    with pytest.raises(ReponseLLMInvalide):
        lancer(service.generate_narrative, FakeProvider(json.dumps(rep)), _narr(indicateurs_calcules=[CA]))
    # valeur précédente manquante → pas de tendance non plus
    manquante = {**CA, "valeur_precedente": {"donnee_manquante": True, "motif": "pas d'historique"}}
    with pytest.raises(ReponseLLMInvalide):
        lancer(service.generate_narrative, FakeProvider(json.dumps(rep)), _narr(indicateurs_calcules=[manquante]))
    # sens contredit par les chiffres
    contredit = {**CA, "valeur_precedente": 20000}
    with pytest.raises(ReponseLLMInvalide):
        lancer(service.generate_narrative, FakeProvider(json.dumps(rep)), _narr(indicateurs_calcules=[contredit]))


def test_tendance_demontree_acceptee():
    rep = {"resume": "x", "analyses": [{"indicateur_id": "ca", "commentaire": "c", "tendance": "hausse"}]}
    r = lancer(service.generate_narrative, FakeProvider(json.dumps(rep)),
               _narr(indicateurs_calcules=[{**CA, "valeur_precedente": 10000}]))
    assert r.analyses[0].tendance == "hausse"


def test_locale_pilote_la_demande_sans_langue_en_dur():
    p = EnregistreurProvider(NARRATION_OK)
    lancer(service.generate_narrative, p,
           _narr(indicateurs_calcules=[CA], donnees_manquantes=[MARGE_MANQUANTE],
                 locale="en-US", objectif_principal="Grow revenue"))
    envoye = json.loads(p.appels[0]["user"])
    assert envoye["locale"] == "en-US"
    assert envoye["objectif_principal"] == "Grow revenue"
    # la langue est un paramètre du prompt, pas une constante du service
    assert "locale" in NARRATIVE_SYSTEM
    assert "locale" in PERFORMANCE_SYSTEM and "locale" in AVAILABILITY_SYSTEM
    source = inspect.getsource(service)
    assert '"fr"' not in source and "français" not in source.lower().replace("en français", "")


def test_reponse_invalide_n_est_pas_gardee_en_cache():
    """Le Gateway cache avant validation : la réponse rejetée doit être écartée."""
    p = EnregistreurProvider("{\"resume\": \"x\", \"analyses\": [{\"indicateur_id\": \"zzz\", \"commentaire\": \"c\"}]}", NARRATION_OK)
    req = _narr(indicateurs_calcules=[CA], donnees_manquantes=[MARGE_MANQUANTE])
    with pytest.raises(ReponseLLMInvalide):
        lancer(service.generate_narrative, p, req)
    r = lancer(service.generate_narrative, p, req)  # 2e tentative → le LLM est rappelé
    assert len(p.appels) == 2
    assert r.meta.cached is False


def test_reponse_valide_est_bien_mise_en_cache():
    p = EnregistreurProvider(NARRATION_OK)
    req = _narr(indicateurs_calcules=[CA], donnees_manquantes=[MARGE_MANQUANTE])
    lancer(service.generate_narrative, p, req)
    r = lancer(service.generate_narrative, p, req)
    assert len(p.appels) == 1 and r.meta.cached is True


# ----- Bulletin ARES / APEX -----
BULLETIN_OK = json.dumps({
    "resume": "Période active.",
    "ares": {"synthese": "120 leads générés.", "points_forts": [], "points_vigilance": [],
             "metriques_citees": ["leads_generes"]},
    "apex": {"synthese": "75 conversations.", "metriques_citees": ["conversations"]},
    "recommandations": [],
})


def _perf(**kw) -> PerformanceInput:
    return PerformanceInput(workspace_id=WID, periode=PERIODE, **kw)


def test_bulletin_ares_apex_valide():
    p = EnregistreurProvider(BULLETIN_OK)
    r = lancer(service.generate_performance_bulletin, p,
               _perf(ares={"leads_generes": 120, "leads_qualifies": 38},
                     apex={"conversations": 75, "tickets_resolus": 61}))
    assert r.ares.metriques_citees == ["leads_generes"]
    assert r.meta.model_used == "claude-sonnet-4-6"
    assert p.appels[0]["system"] == PERFORMANCE_SYSTEM
    envoye = json.loads(p.appels[0]["user"])
    assert envoye["ares"] == {"leads_generes": 120, "leads_qualifies": 38}
    assert "workspace_id" not in envoye


def test_bulletin_zero_vrai_vs_non_disponible():
    p = EnregistreurProvider(json.dumps({
        "resume": "r",
        "ares": {"synthese": "s", "metriques_citees": ["leads_generes"]},
    }))
    lancer(service.generate_performance_bulletin, p, _perf(ares={
        "leads_generes": 0,
        "leads_qualifies": {"donnee_manquante": True, "motif": "CRM non connecté"},
    }))
    ares = json.loads(p.appels[0]["user"])["ares"]
    assert ares["leads_generes"] == 0
    assert ares["leads_qualifies"] == {"statut": "donnee_manquante", "motif": "CRM non connecté"}
    assert "apex" not in json.loads(p.appels[0]["user"])


def test_bulletin_agent_non_fourni_ou_omis_ou_metrique_inconnue_refuse():
    cas = [
        (_perf(ares={"a": 1}), {"resume": "r", "ares": {"synthese": "s"}, "apex": {"synthese": "s"}}),
        (_perf(ares={"a": 1}, apex={"b": 2}), {"resume": "r", "ares": {"synthese": "s"}}),
        (_perf(ares={"a": 1}), {"resume": "r", "ares": {"synthese": "s", "metriques_citees": ["inventee"]}}),
    ]
    for req, rep in cas:
        with pytest.raises(ReponseLLMInvalide):
            lancer(service.generate_performance_bulletin, FakeProvider(json.dumps(rep)), req)


def test_bulletin_json_et_sortie_invalides():
    req = _perf(ares={"a": 1})
    with pytest.raises(ReponseLLMInvalide):
        lancer(service.generate_performance_bulletin, FakeProvider("nope"), req)
    with pytest.raises(ReponseLLMInvalide):
        lancer(service.generate_performance_bulletin, FakeProvider('{"resume": "r"}'), req)


# ----- Disponibilité -----
def _dispo(connectee=True, champs=("montant",)) -> IndicatorAvailabilityInput:
    return IndicatorAvailabilityInput(
        workspace_id=WID, indicateur_id="marge", locale="en",
        sources_requises=[{"nom": "facturation", "connectee": connectee,
                           "champs_requis": ["montant"], "champs_disponibles": list(champs)}],
    )


def test_disponibilite_vrai():
    p = EnregistreurProvider('{"disponible": true, "motif": null}')
    r = lancer(service.check_indicator_availability, p, _dispo())
    assert r.disponible is True and r.motif is None
    assert r.meta.model_used == "claude-haiku-4-5"  # tier fast
    assert json.loads(p.appels[0]["user"])["locale"] == "en"


def test_disponibilite_faux_avec_motif():
    r = lancer(service.check_indicator_availability,
               FakeProvider('{"disponible": false, "motif": "source de facturation non connectée"}'),
               _dispo(connectee=False))
    assert r.disponible is False and r.motif == "source de facturation non connectée"


def test_disponibilite_incoherences_refusees():
    for rep in ('{"disponible": true, "motif": "pourquoi"}', '{"disponible": false, "motif": null}',
                '{"disponible": false}', 'pas du JSON', '{"disponible": "oui", "motif": null}'):
        with pytest.raises(ReponseLLMInvalide):
            lancer(service.check_indicator_availability, FakeProvider(rep), _dispo())


def test_disponible_alors_qu_une_source_est_deconnectee_refuse():
    with pytest.raises(ReponseLLMInvalide):
        lancer(service.check_indicator_availability,
               FakeProvider('{"disponible": true, "motif": null}'), _dispo(connectee=False))
