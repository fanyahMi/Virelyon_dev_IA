"""Validation déterministe des chiffres écrits par Claude : rattachement
indicateur → valeur → unité, normalisation des nombres, exemption des nombres
contextuels. Fonctions pures : aucun LLM, aucun réseau.

Complète tests/test_aura_antihallucination.py (qui teste le service de bout en
bout) ; ici on teste la logique de `app/aura/validation.py` elle-même.
"""
import json

import pytest

from app.aura import service
from app.aura.schemas import NarrativeInput, PerformanceInput
from app.aura.validation import (
    IndicateurRef,
    extraire_nombres,
    normaliser,
    verifier_texte,
)
from app.gateway.cache import response_cache
from app.gateway.provider import ReponseLLMInvalide
from tests.conftest import FakeProvider
from tests.test_aura_service import lancer

WID = "11111111-1111-1111-1111-111111111111"

CA = IndicateurRef("ca", "Chiffre d'affaires", "calcule", 15000.0, (), "EUR")
CLIENTS = IndicateurRef("nouveaux_clients", "Nouveaux clients", "calcule", 12.0, (), "clients")
LEADS = IndicateurRef("leads_generes", "Leads générés", "calcule", 25.0, (), "leads")
CA_MANQUANT = IndicateurRef("ca", "Chiffre d'affaires", "manquant")
CA_ERREUR = IndicateurRef("ca", "Chiffre d'affaires", "erreur")
LEADS_ZERO = IndicateurRef("leads_generes", "Leads générés", "calcule", 0.0, (), "leads")


def ok(texte, refs, **kw):
    assert verifier_texte(texte, refs, **kw) == [], texte


def ko(texte, refs, contient="", **kw):
    problemes = verifier_texte(texte, refs, **kw)
    assert problemes, f"devait être rejeté : {texte}"
    assert contient in " ".join(problemes), problemes


# ===== 1. Rattachement indicateur → valeur → unité =====
def test_valeur_d_un_autre_indicateur_est_refusee():
    base = [CA, CLIENTS]
    ko("Le CA est de 12 EUR.", base, "ne correspond pas")            # 12 existe, mais c'est les clients
    ko("Le chiffre d'affaires est de 12 EUR.", base)
    ko("Les nouveaux clients sont au nombre de 15 000.", base)       # 15000 existe, mais c'est le CA
    ok("Le CA est de 15 000 EUR et nous comptons 12 nouveaux clients.", base)
    ok("12 nouveaux clients ont été enregistrés et le CA atteint 15 000 EUR.", base)
    ok("Le CA atteint 15 000 EUR pour 12 nouveaux clients.", base)


def test_unite_incompatible_est_refusee():
    ko("Les nouveaux clients représentent 12 EUR.", [CA, CLIENTS], "unité")
    ko("Le CA est de 15000 clients.", [CA, CLIENTS])          # rattaché aux clients : 15000 ≠ 12
    ko("Le CA est de 15 000 %.", [CA, CLIENTS], "unité")
    ok("Le CA est de 15 000 €.", [CA, CLIENTS])
    ok("Le CA est de 15 000 euros.", [CA, CLIENTS])


def test_commentaire_d_analyse_rattache_a_son_indicateur_sans_mention():
    ko("Atteint 12 EUR sur la période.", [CA, CLIENTS], "ne correspond pas", defaut="ca")
    ok("Atteint 15 000 EUR sur la période.", [CA, CLIENTS], defaut="ca")


def test_valeur_precedente_acceptee_seulement_quand_on_compare():
    ca = IndicateurRef("ca", "Chiffre d'affaires", "calcule", 15000.0, (12000.0,), "EUR")
    ok("Le CA passe de 12 000 EUR à 15 000 EUR.", [ca])
    ok("Le CA atteint 15 000 EUR contre 12 000 EUR auparavant.", [ca])
    ko("Le CA est de 12 000 EUR.", [ca])           # la valeur précédente présentée comme actuelle


def test_variation_ou_ratio_calcule_par_claude_refuse():
    ko("Le CA a progressé de 25 %.", [CA, CLIENTS])
    ko("Soit 1 250 EUR par client.", [CA, CLIENTS])
    ko("Les clients et le CA totalisent 15 012.", [CA, CLIENTS])


# ===== 2. Le 0 d'un autre indicateur ne valide jamais le 0 du CA =====
@pytest.mark.parametrize("texte", [
    "Le CA est de 0 EUR.",
    "Le chiffre d'affaires est de 0 €.",
    "Le CA vaut 0.",
    "Aucun lead et un CA de 0 EUR.",
    "0 EUR de chiffre d'affaires.",
    "Le total est de 0 EUR.",                      # non rattaché, mais en EUR : aucun indicateur EUR à 0
])
def test_zero_d_un_autre_indicateur_ne_valide_pas_le_ca(texte):
    ko(texte, [CA_MANQUANT, LEADS_ZERO])


def test_zero_reste_valide_pour_l_indicateur_qui_vaut_zero():
    ok("0 lead généré sur la période.", [CA_MANQUANT, LEADS_ZERO])
    ok("Le CA est indisponible ; les leads générés sont à 0.", [CA_MANQUANT, LEADS_ZERO])
    ko("Les leads générés sont à 5.", [CA_MANQUANT, LEADS_ZERO])


def test_sigle_derive_du_libelle_rattache_meme_si_l_identifiant_differe():
    ca = IndicateurRef("chiffre_affaires_mensuel", "Chiffre d'affaires", "manquant")
    ko("Le CA est de 0 EUR.", [ca, LEADS_ZERO])   # « CA » = initiales du libellé


# ===== 3-6. Normalisation des nombres =====
@pytest.mark.parametrize("ecriture", [
    "15000", "15 000", "15 000", "15.000", "15,000", "15000,00", "15 000,0",
    "15k", "15 K", "15 k", "15,0k", "15 mille", "quinze mille", "Quinze Mille",
    "fifteen thousand", "15 thousand",
])
def test_formes_equivalentes_a_15000_acceptees(ecriture):
    ok(f"Le CA est de {ecriture} EUR.", [CA, CLIENTS])


@pytest.mark.parametrize("ecriture", ["15 001", "14k", "15,4k", "quinze mille un", "seize mille", "1 500", "15"])
def test_formes_differentes_de_15000_refusees(ecriture):
    ko(f"Le CA est de {ecriture} EUR.", [CA, CLIENTS])


def test_k_exact_quand_la_valeur_est_exacte():
    ca = IndicateurRef("ca", "Chiffre d'affaires", "calcule", 15400.0, (), "EUR")
    ok("Le CA est de 15,4k EUR.", [ca])
    ko("Le CA est de 15k EUR.", [ca])            # 15k ≠ 15400 : arrondi interdit


@pytest.mark.parametrize("texte", [
    "Le CA est d'environ 15k EUR.",
    "Le CA est d'environ 15 000 EUR.",
    "Le CA est de ~15 000 EUR.",
    "Le CA est de ≈ 15000 EUR.",
    "Le CA dépasse 15 000 EUR, plus de 15 000 EUR.",
    "Le CA est de 15 000 EUR environ.",
    "Le CA s'élève à près de 15 000 EUR.",
    "Le CA est estimé à 15 000 EUR.",
    "The revenue is roughly 15,000 EUR; CA is about 15k EUR.",
])
def test_approximation_jamais_equivalente_a_la_valeur_exacte(texte):
    ko(texte, [CA, CLIENTS], "approximation")


def test_nombres_en_lettres_francais_et_anglais():
    val = lambda t: sorted(v for n in extraire_nombres(normaliser(t)) for v in n.valeurs)
    assert val("quinze mille") == [15000.0]
    assert val("vingt-cinq") == [25.0]
    assert val("soixante et onze") == [71.0]
    assert val("quatre-vingt-dix-sept") == [97.0]
    assert val("quatre-vingts") == [80.0]
    assert val("deux cent trente") == [230.0]
    assert val("mille deux cent trente-quatre") == [1234.0]
    assert val("trois mille deux cents") == [3200.0]
    assert val("deux millions") == [2_000_000.0]
    assert val("two thousand twenty six") == [2026.0]
    assert val("twenty-five") == [25.0]
    assert val("zéro") == [0.0]
    assert val("un indicateur et une donnée") == []          # articles : pas des nombres
    assert val("15 mille") == [15000.0]                       # pas de double comptage
    assert val("quinze pour cent") == [15.0]                  # « cent » du pourcentage ≠ 100


def test_nombre_en_lettres_est_controle_comme_un_chiffre():
    ok("Le CA est de quinze mille EUR.", [CA, CLIENTS])
    ok("Nous comptons douze nouveaux clients.", [CA, CLIENTS])
    ko("Nous comptons treize nouveaux clients.", [CA, CLIENTS])
    ko("Le CA est de vingt mille EUR.", [CA, CLIENTS])
    ko("Le CA est de zéro EUR.", [CA_MANQUANT, LEADS_ZERO])
    ko("Le CA est de quinze pour cent.", [CA, CLIENTS])


# ===== 7-9. Faux positifs : nombres contextuels exemptés =====
@pytest.mark.parametrize("texte", [
    "Voici le top 3 des priorités.",
    "Au T3, le CA atteint 15 000 EUR.",
    "Au Q3 2026, le CA atteint 15 000 EUR.",
    "Au 1er trimestre, le CA atteint 15 000 EUR.",
    "Au premier trimestre 2026, le CA atteint 15 000 EUR.",
    "Au 3e trimestre, 12 nouveaux clients.",
    "En 2026, le CA est de 15 000 EUR.",
    "Du 1er au 30 septembre 2026, le CA atteint 15 000 EUR.",
    "Période du 2026-09-01 au 2026-09-30 : 12 nouveaux clients.",
    "Depuis 2 semaines, 12 nouveaux clients.",
    "Sur les 3 indicateurs fournis, le CA atteint 15 000 EUR.",
    "Le rapport couvre 2 agents et 5 sources.",
    "Version 2 du rapport, étape 4, page 7.",
])
def test_nombres_contextuels_non_rejetes(texte):
    ok(texte, [CA, CLIENTS])


def test_un_nombre_contextuel_presente_comme_kpi_reste_controle():
    ko("Le CA est de 2026 EUR.", [CA, CLIENTS])      # année avec unité monétaire = valeur de KPI
    ko("Le CA est de 30 %.", [CA, CLIENTS])


def test_nombre_inconnu_non_rattache_sans_unite_reste_refuse():
    ko("Soit 195 échanges au total.", [CA, CLIENTS], "absent des données")
    ok("Soit 12 en tout.", [CA, CLIENTS])


# ===== 10. Donnée manquante =====
@pytest.mark.parametrize("texte", [
    "Le CA est de 0 EUR.",
    "Le CA est estimé à 14 000 EUR.",
    "Le CA moyen est de 13 500 EUR.",
    "Le CA est d'environ 15 000 EUR.",
    "Le chiffre d'affaires atteint quinze mille euros.",
])
def test_donnee_manquante_aucun_chiffre_attribuable(texte):
    ko(texte, [CA_MANQUANT], "manquant")


def test_donnee_manquante_signalee_sans_chiffre_acceptee():
    ok("Le chiffre d'affaires est indisponible : source de facturation non connectée.", [CA_MANQUANT])
    ok("Au T3 2026, le CA est indisponible depuis 2 semaines.", [CA_MANQUANT])


# ===== 11. Erreur de calcul =====
@pytest.mark.parametrize("texte", ["Le CA est de 0 EUR.", "Le CA vaut 15 000 EUR.", "CA : zéro."])
def test_erreur_de_calcul_jamais_transformee_en_valeur(texte):
    ko(texte, [CA_ERREUR, LEADS_ZERO], "en erreur")


def test_erreur_de_calcul_signalee_acceptee():
    ok("Le calcul du chiffre d'affaires a échoué : délai dépassé.", [CA_ERREUR, LEADS_ZERO])


# ===== 12-13. Pydantic + réponse Claude invalide, via le service =====
def _narr(**kw):
    return NarrativeInput(workspace_id=WID, **kw)


CA_IN = {"id": "ca", "libelle": "Chiffre d'affaires", "valeur": 15000, "unite": "EUR"}
CLIENTS_IN = {"id": "nouveaux_clients", "libelle": "Nouveaux clients", "valeur": 12, "unite": "clients"}
CA_MANQ_IN = {"id": "ca", "libelle": "Chiffre d'affaires", "motif": "source de facturation non connectée"}


def rep(resume, analyses=(), manquantes=()):
    return json.dumps({"resume": resume, "analyses": list(analyses), "donnees_manquantes": list(manquantes)})


def appeler(brut, req):
    response_cache.clear()
    return lancer(service.generate_narrative, FakeProvider(brut), req)


def test_service_rejette_le_mauvais_rattachement():
    req = _narr(indicateurs_calcules=[CA_IN, CLIENTS_IN])
    with pytest.raises(ReponseLLMInvalide, match="ne correspond pas"):
        appeler(rep("Le CA est de 12 EUR."), req)
    appeler(rep("Le CA est de 15 000 EUR avec 12 nouveaux clients, au T3 2026 (top 3)."), req)


def test_service_rejette_le_zero_d_un_autre_indicateur():
    req = _narr(indicateurs_calcules=[{"id": "leads_generes", "libelle": "Leads générés", "valeur": 0,
                                       "unite": "leads"}], donnees_manquantes=[CA_MANQ_IN])
    with pytest.raises(ReponseLLMInvalide, match="manquant"):
        appeler(rep("Le CA est de 0 EUR.", manquantes=[CA_MANQ_IN]), req)
    appeler(rep("0 lead généré ; le CA est indisponible.", manquantes=[CA_MANQ_IN]), req)


def test_service_rejette_une_approximation():
    with pytest.raises(ReponseLLMInvalide, match="approximation"):
        appeler(rep("Le CA est d'environ 15k EUR."), _narr(indicateurs_calcules=[CA_IN]))
    appeler(rep("Le CA est de 15k EUR."), _narr(indicateurs_calcules=[CA_IN]))


def test_service_commentaire_rattache_a_l_indicateur_de_l_analyse():
    req = _narr(indicateurs_calcules=[CA_IN, CLIENTS_IN])
    mauvais = rep("Synthèse.", analyses=[{"indicateur_id": "ca", "commentaire": "Atteint 12 EUR."}])
    with pytest.raises(ReponseLLMInvalide):
        appeler(mauvais, req)
    bon = rep("Synthèse.", analyses=[{"indicateur_id": "ca", "commentaire": "Atteint 15 000 EUR."}])
    appeler(bon, req)


@pytest.mark.parametrize("brut", ["pas du JSON", '{"analyses": []}', '{"resume": "x", "extra": 1}', ""])
def test_service_reponse_invalide_ou_hors_schema(brut):
    with pytest.raises(ReponseLLMInvalide):
        appeler(brut, _narr(indicateurs_calcules=[CA_IN]))


def test_bulletin_agregat_manquant_jamais_zero_et_contrat_inchange():
    assert "statut" not in PerformanceInput.model_fields           # pas de statut « erreur » ajouté
    req = PerformanceInput(workspace_id=WID, periode={"debut": "2026-09-01", "fin": "2026-09-30"},
                           apex={"conversations": 75,
                                 "tickets_resolus": {"donnee_manquante": True,
                                                     "motif": "erreur technique lors du calcul"}})
    def bulletin(texte):
        return json.dumps({"resume": "r", "apex": {"synthese": texte,
                                                     "metriques_citees": ["conversations"]}})
    response_cache.clear()
    lancer(service.generate_performance_bulletin, FakeProvider(bulletin(
        "75 conversations ; tickets résolus indisponibles (erreur technique).")), req)
    response_cache.clear()
    with pytest.raises(ReponseLLMInvalide, match="manquant"):
        lancer(service.generate_performance_bulletin, FakeProvider(bulletin(
            "75 conversations et 0 ticket résolu.")), req)
