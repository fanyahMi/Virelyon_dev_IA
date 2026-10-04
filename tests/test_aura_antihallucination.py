"""Garde-fous anti-hallucination d'AURA : l'IA ne peut ni inventer, ni modifier,
ni calculer un chiffre, ni transformer une donnée manquante ou en erreur en valeur.

Provider mocké : on SIMULE ce que Claude pourrait répondre de faux et on vérifie
que le service le rejette (ReponseLLMInvalide → HTTP 502). Aucun appel réel.

Déjà couvert ailleurs (non recréé ici) : tests/test_aura_service.py (tendances,
JSON invalide, schéma de sortie, cache, bulletin et disponibilité de base),
tests/test_aura_schemas.py (0 ≠ manquant, formats d'entrée) et
tests/test_aura_api.py (422/502/429/503, isolation du workspace).
"""
import ast
import json
import pathlib

import pytest
from pydantic import ValidationError

from app.aura import service
from app.aura.schemas import (
    IndicatorAvailabilityInput,
    NarrativeInput,
    NarrativeOutput,
    PerformanceInput,
)
from app.gateway.cache import response_cache
from app.gateway.provider import ReponseLLMInvalide, get_provider
from app.main import app
from tests.conftest import HEADERS, FakeProvider
from tests.test_aura_service import EnregistreurProvider, lancer

WID = "11111111-1111-1111-1111-111111111111"
PERIODE = {"debut": "2026-09-01", "fin": "2026-09-30"}

CA = {"id": "ca", "libelle": "Chiffre d'affaires", "valeur": 15000, "unite": "EUR"}
CLIENTS = {"id": "nouveaux_clients", "libelle": "Nouveaux clients", "valeur": 12, "unite": "clients"}
LEADS = {"id": "leads_generes", "libelle": "Leads générés", "valeur": 25, "unite": "leads"}
CA_MANQUANT = {"id": "ca", "libelle": "Chiffre d'affaires", "motif": "source de facturation non connectée"}
CA_ERREUR = {"id": "ca", "libelle": "Chiffre d'affaires", "motif": "délai dépassé lors du calcul"}


def narr(**kw) -> NarrativeInput:
    return NarrativeInput(workspace_id=WID, **kw)


def reponse(resume="Synthèse.", analyses=(), attention=(), manquantes=(), erreurs=()) -> str:
    return json.dumps({
        "resume": resume,
        "analyses": [{"indicateur_id": i, "commentaire": c, "tendance": "indeterminee"} for i, c in analyses],
        "points_attention": list(attention),
        "donnees_manquantes": list(manquantes),
        "indicateurs_en_erreur": list(erreurs),
    })


def appeler(texte, req):
    return lancer(service.generate_narrative, FakeProvider(texte), req)


# ===== Étape 2 : KPI calculés — valeurs exactes =====
def test_plusieurs_kpi_valeurs_exactes_envoyees_et_restituees():
    p = EnregistreurProvider(reponse(
        "Le CA est de 15 000 EUR, avec 12 nouveaux clients et 25 leads générés.",
        analyses=[("ca", "15000 EUR."), ("nouveaux_clients", "12 clients."), ("leads_generes", "25 leads.")]))
    r = lancer(service.generate_narrative, p, narr(indicateurs_calcules=[CA, CLIENTS, LEADS]))
    envoye = {i["id"]: i["valeur"] for i in json.loads(p.appels[0]["user"])["indicateurs_calcules"]}
    assert envoye == {"ca": 15000, "nouveaux_clients": 12, "leads_generes": 25}  # ni arrondi ni modifié
    assert "15 000" in r.resume and "12" in r.resume and "25" in r.resume


@pytest.mark.parametrize("ecriture", ["15 000", "15000", "15.000", "15,000", "15 000", "15000,00", "15 000 EUR"])
def test_formats_d_ecriture_d_une_valeur_fournie_acceptes(ecriture):
    appeler(reponse(f"Le CA atteint {ecriture}."), narr(indicateurs_calcules=[CA]))


@pytest.mark.parametrize("texte", [
    "Le CA atteint 15 200 EUR.",              # modifié
    "Le CA atteint environ 15 000 EUR et 12,5 clients.",   # 12,5 : valeur inventée
    "Les 25 leads donnent 48 % de conversion.",            # ratio calculé par le modèle
    "Le CA et les clients totalisent 15 012.",             # somme calculée
    "Le CA a augmenté de 10 %.",                           # variation non fournie
])
def test_chiffre_modifie_arrondi_ou_calcule_est_refuse(texte):
    with pytest.raises(ReponseLLMInvalide, match="chiffre"):
        appeler(reponse(texte), narr(indicateurs_calcules=[CA, CLIENTS, LEADS]))


# ===== Étape 8 : CA fourni = 15000, Claude répond 20000 =====
@pytest.mark.parametrize("brut", [
    reponse("Le CA est de 20000 EUR."),
    reponse("Bon mois.", analyses=[("ca", "Le CA = 20000.")]),
    reponse("Bon mois.", attention=["Le CA de 20 000 EUR est à surveiller."]),
])
def test_valeur_fournie_contredite_par_claude_est_incorrecte(brut):
    with pytest.raises(ReponseLLMInvalide):
        appeler(brut, narr(indicateurs_calcules=[CA]))


def test_reponse_contredisant_les_donnees_n_est_pas_gardee_en_cache():
    p = EnregistreurProvider(reponse("Le CA est de 20000 EUR."), reponse("Le CA est de 15000 EUR."))
    req = narr(indicateurs_calcules=[CA])
    response_cache.clear()
    with pytest.raises(ReponseLLMInvalide):
        lancer(service.generate_narrative, p, req)
    assert lancer(service.generate_narrative, p, req).resume == "Le CA est de 15000 EUR."
    assert len(p.appels) == 2


def test_periode_annee_et_decomptes_ne_sont_pas_des_chiffres_inventes():
    appeler(reponse("Du 1er au 30 septembre 2026, 2 indicateurs sur 3 sont calculés.",
                    manquantes=[{"id": "marge", "libelle": "Marge", "motif": "x"}]),
            narr(periode=PERIODE, indicateurs_calcules=[CA, CLIENTS], donnees_manquantes=[
                {"id": "marge", "libelle": "Marge", "motif": "x"}]))
    # une année n'est pas une valeur de KPI : exemptée même sans période fournie
    appeler(reponse("Rapport 2026."), narr(indicateurs_calcules=[CA]))


def test_nombre_present_dans_un_texte_fourni_par_le_backend_est_autorise():
    req = narr(objectif_principal="Atteindre 20 000 EUR de CA au T3", indicateurs_calcules=[CA])
    appeler(reponse("Objectif de 20 000 EUR ; CA actuel de 15000 EUR (T3)."), req)
    response_cache.clear()  # même entrée : sinon la 1re réponse serait resservie
    with pytest.raises(ReponseLLMInvalide):  # 18 000 n'est nulle part dans l'entrée
        appeler(reponse("Objectif de 18 000 EUR."), req)


# ===== Étape 3 : le zéro =====
def test_zero_calcule_est_une_vraie_valeur():
    leads0 = {**LEADS, "valeur": 0}
    p = EnregistreurProvider(reponse("Aucun lead : 0 lead généré.", analyses=[("leads_generes", "Zéro, soit 0 lead.")]))
    r = lancer(service.generate_narrative, p, narr(indicateurs_calcules=[leads0]))
    envoye = json.loads(p.appels[0]["user"])
    assert envoye["indicateurs_calcules"][0]["valeur"] == 0
    assert envoye["indicateurs_calcules"][0]["statut"] == "calcule"
    assert envoye["donnees_manquantes"] == [] and envoye["indicateurs_en_erreur"] == []
    assert r.donnees_manquantes == []  # le zéro n'a pas été reclassé en manquant


def test_zero_fourni_mais_claude_invente_une_autre_valeur():
    with pytest.raises(ReponseLLMInvalide):
        appeler(reponse("Seulement 5 leads."), narr(indicateurs_calcules=[{**LEADS, "valeur": 0}]))


# ===== Étape 4 : donnée manquante =====
def test_donnee_manquante_signalee_sans_chiffre():
    p = EnregistreurProvider(reponse(
        "Le chiffre d'affaires est indisponible : source de facturation non connectée.",
        manquantes=[CA_MANQUANT]))
    r = lancer(service.generate_narrative, p, narr(donnees_manquantes=[CA_MANQUANT]))
    envoye = json.loads(p.appels[0]["user"])["donnees_manquantes"][0]
    assert envoye["statut"] == "donnee_manquante" and "valeur" not in envoye
    assert r.donnees_manquantes[0].motif == "source de facturation non connectée"


@pytest.mark.parametrize("texte", [
    "Le chiffre d'affaires est de 0 EUR.",
    "Le CA est estimé à 14000 EUR.",
    "Le CA moyen serait de 13 500 EUR.",
    "Le CA est d'environ 15 000 EUR.",
])
def test_donnee_manquante_jamais_zero_estimee_moyenne_ou_approchee(texte):
    with pytest.raises(ReponseLLMInvalide):
        appeler(reponse(texte, manquantes=[CA_MANQUANT]), narr(donnees_manquantes=[CA_MANQUANT]))


# ===== Étape 5 : indicateur en erreur =====
def test_erreur_de_calcul_signalee_explicitement():
    p = EnregistreurProvider(reponse(
        "Le calcul du chiffre d'affaires a échoué : délai dépassé.", erreurs=[CA_ERREUR]))
    r = lancer(service.generate_narrative, p, narr(indicateurs_en_erreur=[CA_ERREUR]))
    envoye = json.loads(p.appels[0]["user"])
    assert envoye["indicateurs_en_erreur"] == [{**CA_ERREUR, "statut": "erreur"}]
    assert "valeur" not in envoye["indicateurs_en_erreur"][0]
    assert envoye["indicateurs_calcules"] == [] and envoye["donnees_manquantes"] == []
    assert r.indicateurs_en_erreur[0].motif == "délai dépassé lors du calcul"


@pytest.mark.parametrize("brut", [
    reponse("Le CA est de 0 EUR.", erreurs=[CA_ERREUR]),                        # erreur → valeur
    reponse("Rapport.", erreurs=[]),                                            # erreur masquée
    reponse("Rapport.", erreurs=[CA_ERREUR, {**CA_ERREUR, "id": "inventee"}]),   # erreur ajoutée
    reponse("Rapport.", analyses=[("ca", "Analyse du CA.")], erreurs=[CA_ERREUR]),  # commentée comme calculée
    reponse("Rapport.", manquantes=[CA_MANQUANT]),                              # erreur reclassée en manquant
])
def test_erreur_jamais_transformee_masquee_ni_confondue(brut):
    with pytest.raises(ReponseLLMInvalide):
        appeler(brut, narr(indicateurs_en_erreur=[CA_ERREUR]))


def test_les_trois_statuts_coexistent():
    req = narr(indicateurs_calcules=[CLIENTS],
               donnees_manquantes=[{"id": "marge", "libelle": "Marge", "motif": "x"}],
               indicateurs_en_erreur=[CA_ERREUR])
    r = appeler(reponse("12 clients ; marge manquante ; CA en erreur.",
                        analyses=[("nouveaux_clients", "12.")],
                        manquantes=[{"id": "marge", "libelle": "Marge", "motif": "x"}], erreurs=[CA_ERREUR]), req)
    assert [e.id for e in r.indicateurs_en_erreur] == ["ca"] and r.donnees_manquantes[0].id == "marge"


# ===== Étape 6 : identifiants =====
@pytest.mark.parametrize("corps", [
    dict(indicateurs_calcules=[CA], donnees_manquantes=[CA_MANQUANT]),
    dict(indicateurs_calcules=[CA], indicateurs_en_erreur=[CA_ERREUR]),
    dict(donnees_manquantes=[CA_MANQUANT], indicateurs_en_erreur=[CA_ERREUR]),
    dict(indicateurs_calcules=[CA, CA]),
    dict(donnees_manquantes=[CA_MANQUANT, CA_MANQUANT]),
    dict(indicateurs_en_erreur=[CA_ERREUR, CA_ERREUR]),
])
def test_identifiant_unique_et_dans_une_seule_liste(corps):
    with pytest.raises(ValidationError, match="identifiant"):
        narr(**corps)


def test_identifiants_en_double_refuses_par_l_api(client):
    r = client.post("/api/v1/aura/narrative", headers=HEADERS, json={
        "workspace_id": WID, "indicateurs_calcules": [CA], "donnees_manquantes": [CA_MANQUANT]})
    assert r.status_code == 422


# ===== Étape 7 : réponse Claude invalide =====
@pytest.mark.parametrize("brut", [
    '{"resume": "", "analyses": []}',                                              # résumé vide
    '{"resume": "x", "analyses": "aucune"}',                                       # mauvais type
    '{"resume": "x", "analyses": [{"commentaire": "c"}]}',                         # id absent
    '{"resume": "x", "indicateurs_en_erreur": [{"id": "ca"}]}',                    # erreur mal formée
    '{"resume": "x", "analyses": [], "tendance_globale": "hausse"}',               # champ hors contrat
    "```json\n{\"resume\": \"x\", \n```",                                          # JSON tronqué
    "",
])
def test_reponse_hors_schema_jamais_interpretee_librement(brut):
    with pytest.raises(ReponseLLMInvalide):
        appeler(brut, narr(indicateurs_calcules=[CA]))


def test_schema_de_sortie_refuse_directement_une_reponse_incorrecte():
    with pytest.raises(ValidationError):
        NarrativeOutput.model_validate({"resume": "x", "analyses": [], "autre": 1})


# ===== Étape 9 : bulletin ARES/APEX — agrégats déjà calculés =====
# Noms inspirés des journaux lead_events / conversation_events, mais AUCUNE lecture
# de ces tables : le backend envoie des agrégats, AURA ne fait que raconter.
AGREGATS_ARES = {"leads_generes": 120, "leads_qualifies": 38, "taux_qualification": 31.67}
AGREGATS_APEX = {"conversations": 75, "tickets_resolus": 61}


def perf(**kw) -> PerformanceInput:
    return PerformanceInput(workspace_id=WID, periode=PERIODE, ares=AGREGATS_ARES, apex=AGREGATS_APEX, **kw)


def bulletin(resume="Bulletin.", ares="Synthèse ARES.", apex="Synthèse APEX.", reco=()):
    return json.dumps({
        "resume": resume,
        "ares": {"synthese": ares, "metriques_citees": ["leads_generes"]},
        "apex": {"synthese": apex, "metriques_citees": ["conversations"]},
        "recommandations": list(reco),
    })


def test_bulletin_envoie_les_agregats_tels_quels_sans_rien_calculer():
    p = EnregistreurProvider(bulletin(
        ares="120 leads générés, 38 qualifiés, taux de qualification de 31,67 %.",
        apex="75 conversations et 61 tickets résolus."))
    lancer(service.generate_performance_bulletin, p, perf())
    envoye = json.loads(p.appels[0]["user"])
    assert envoye["ares"] == AGREGATS_ARES and envoye["apex"] == AGREGATS_APEX
    assert set(envoye) == {"periode", "locale", "ares", "apex"}  # aucun total, ratio ni champ calculé ajouté


@pytest.mark.parametrize("texte", [
    "Soit 195 échanges au total.",                    # somme 120 + 75 calculée par le modèle
    "Le taux de résolution est de 81,3 %.",           # 61 / 75 recalculé
    "Seulement 31 % des leads sont qualifiés.",       # taux recalculé et arrondi
    "Les leads ont augmenté de 20 %.",                # variation inventée
])
def test_bulletin_refuse_les_statistiques_recalculees(texte):
    for cible in ("resume", "ares", "apex", "reco"):
        brut = bulletin(**{cible: [texte] if cible == "reco" else texte})
        with pytest.raises(ReponseLLMInvalide, match="chiffre"):
            lancer(service.generate_performance_bulletin, FakeProvider(brut), perf())


def test_bulletin_zero_et_manquant_ne_se_confondent_pas():
    req = PerformanceInput(workspace_id=WID, periode=PERIODE, ares={
        "leads_generes": 0, "leads_qualifies": {"donnee_manquante": True, "motif": "CRM non connecté"}})
    def rep(synthese):
        return json.dumps({"resume": "r", "ares": {
            "synthese": synthese, "metriques_citees": ["leads_generes", "leads_qualifies"]}})

    lancer(service.generate_performance_bulletin,
           FakeProvider(rep("0 lead ; qualifiés indisponibles (CRM non connecté).")), req)
    response_cache.clear()  # même entrée : sinon la 1re réponse valide serait resservie
    with pytest.raises(ReponseLLMInvalide):  # manquant → valeur inventée
        lancer(service.generate_performance_bulletin, FakeProvider(rep("0 lead ; 38 qualifiés.")), req)


# ===== Étape 10 : disponibilité =====
def _dispo(connectee: bool, champs=("montant",)):
    return IndicatorAvailabilityInput(workspace_id=WID, indicateur_id="ca", sources_requises=[{
        "nom": "facturation", "connectee": connectee, "champs_requis": ["montant"],
        "champs_disponibles": list(champs)}])


def test_disponibilite_vrai_motif_null_et_faux_motif_explicite():
    vrai = lancer(service.check_indicator_availability,
                  FakeProvider('{"disponible": true, "motif": null}'), _dispo(True))
    assert vrai.model_dump(exclude={"meta"}) == {"disponible": True, "motif": None}
    faux = lancer(service.check_indicator_availability,
                  FakeProvider('{"disponible": false, "motif": "source de facturation non connectée"}'), _dispo(False, ()))
    assert faux.model_dump(exclude={"meta"}) == {
        "disponible": False, "motif": "source de facturation non connectée"}
    with pytest.raises(ReponseLLMInvalide):  # indisponible sans motif
        lancer(service.check_indicator_availability, FakeProvider('{"disponible": false, "motif": null}'), _dispo(False))


# ===== Étape 11 : locale, et aucun ton de voix =====
@pytest.mark.parametrize("locale", ["fr", "en-US", "es", "de-DE"])
def test_locale_transmise_aux_trois_prompts(locale):
    p = EnregistreurProvider(reponse("Report 15000."))
    lancer(service.generate_narrative, p, NarrativeInput(workspace_id=WID, locale=locale, indicateurs_calcules=[CA]))
    assert json.loads(p.appels[0]["user"])["locale"] == locale
    p = EnregistreurProvider(bulletin())
    lancer(service.generate_performance_bulletin, p, PerformanceInput(
        workspace_id=WID, periode=PERIODE, locale=locale, ares=AGREGATS_ARES, apex=AGREGATS_APEX))
    assert json.loads(p.appels[0]["user"])["locale"] == locale
    p = EnregistreurProvider('{"disponible": true, "motif": null}')
    lancer(service.check_indicator_availability, p, IndicatorAvailabilityInput(
        workspace_id=WID, locale=locale, indicateur_id="ca",
        sources_requises=[{"nom": "f", "connectee": True}]))
    assert json.loads(p.appels[0]["user"])["locale"] == locale


def test_la_locale_est_une_consigne_de_langue_dans_chaque_prompt():
    from app.prompts.aura import AVAILABILITY_SYSTEM, NARRATIVE_SYSTEM, PERFORMANCE_SYSTEM
    for prompt in (NARRATIVE_SYSTEM, PERFORMANCE_SYSTEM, AVAILABILITY_SYSTEM):
        assert "locale" in prompt


def test_aucun_ton_de_voix_dans_aura():
    racine = pathlib.Path(__file__).resolve().parents[1]
    fichiers = [*(racine / "app" / "aura").glob("*.py"), racine / "app/prompts/aura.py",
                racine / "app/api/v1/endpoints/aura.py", *(racine / "tests").glob("test_aura_*.py")]
    for f in fichiers:
        if f.name == pathlib.Path(__file__).name:
            continue  # ce fichier cite le mot pour le chercher
        assert "ton_de_voix" not in f.read_text(encoding="utf-8"), f
    assert "ton_de_voix" not in NarrativeInput.model_fields and "ton_de_voix" not in PerformanceInput.model_fields


# ===== Périmètre : aucune base, aucun connecteur, aucun client LLM propre =====
def test_aura_n_importe_ni_base_ni_connecteur_ni_client_llm():
    interdits = ("sqlalchemy", "asyncpg", "psycopg", "alembic", "app.db", "app.sourcing",
                 "anthropic", "httpx", "requests", "stripe", "hubspot", "simple_salesforce")
    racine = pathlib.Path(__file__).resolve().parents[1]
    for f in [*(racine / "app" / "aura").glob("*.py"), racine / "app/api/v1/endpoints/aura.py"]:
        for noeud in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            modules = ([a.name for a in noeud.names] if isinstance(noeud, ast.Import)
                       else [noeud.module or ""] if isinstance(noeud, ast.ImportFrom) else [])
            for m in modules:
                assert not any(m == i or m.startswith(i + ".") for i in interdits), (f.name, m)
