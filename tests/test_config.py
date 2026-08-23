"""Tests du chargement de configuration client.

L'enjeu réel de ces tests : vérifier que **deux clients obtiennent des
comportements différents** avec le même code. Sans ça, l'Agent Builder ne sert
à rien.
"""
from uuid import UUID

from app.builder.config import PALIERS_PAR_DEFAUT, charger_config, config_par_defaut

TEST_ARES = "11111111-1111-1111-1111-111111111111"
RESTAURATION = "096c84c1-cf60-4c2b-ab87-8132570a59f4"
CYCLE_LONG = "25db28de-c378-4878-a51a-129016ccfc7f"
INCONNU = "99999999-9999-9999-9999-999999999999"


# ----- Client sans configuration ---------------------------------------------
def test_client_inconnu_recoit_un_defaut_prudent():
    """Un compte qui vient d'être créé n'est pas une erreur."""
    cfg = charger_config(INCONNU)
    assert cfg.statut == "configuration_incomplete"
    assert cfg.autonomy_level == "supervision"  # jamais d'envoi automatique
    assert cfg.canaux_actifs == ["email"]
    assert cfg.paliers == PALIERS_PAR_DEFAUT


def test_le_defaut_ne_cible_personne():
    cfg = config_par_defaut(INCONNU)
    assert cfg.icp.secteurs_inclus == []
    assert cfg.icp.taille_min is None


# ----- Chargement d'une configuration réelle ---------------------------------
def test_config_de_reference_est_chargee():
    cfg = charger_config(TEST_ARES)
    assert cfg.workspace_id == UUID(TEST_ARES)
    assert cfg.statut == "actif"
    assert cfg.ton_de_voix == "professionnel"
    assert cfg.zone == "Genève"
    assert "conseil" in cfg.icp.secteurs_inclus
    assert "hotellerie" in cfg.icp.secteurs_exclus
    assert cfg.objectif_principal  # non vide : alimente les prompts


def test_paliers_par_defaut_quand_le_client_ne_les_personnalise_pas():
    assert charger_config(TEST_ARES).paliers == PALIERS_PAR_DEFAUT


# ----- Le point qui compte : deux clients, deux comportements ----------------
def test_deux_clients_ont_des_comportements_differents():
    a, b = charger_config(TEST_ARES), charger_config(RESTAURATION)
    assert a.ton_de_voix != b.ton_de_voix
    assert a.autonomy_level != b.autonomy_level
    assert a.canaux_actifs != b.canaux_actifs
    assert a.seuil_confiance != b.seuil_confiance
    assert set(a.icp.secteurs_inclus).isdisjoint(b.icp.secteurs_inclus)


def test_un_client_peut_cibler_un_marche_hors_icp_de_la_plateforme():
    """Le référentiel garantit la cohérence du vocabulaire, pas le périmètre."""
    cfg = charger_config(RESTAURATION)
    assert cfg.icp.secteurs_inclus == ["restauration", "hotellerie"]


def test_les_ponderations_de_score_sont_par_client():
    assert charger_config(RESTAURATION).scoring_config.poids_fit == 0.50
    assert charger_config(TEST_ARES).scoring_config.poids_fit == 0.40  # défaut


# ----- Paliers personnalisés : la cadence vient de la config, pas du code ----
def test_paliers_personnalises_remplacent_ceux_du_cdcf():
    cfg = charger_config(CYCLE_LONG)
    assert [e.palier.nom for e in cfg.paliers] == ["prioritaire", "standard", "veille"]
    assert cfg.palier_pour(95).relances_max == 6
    assert cfg.palier_pour(95).cadence[-1] == 60  # cycle long


def test_palier_pour_selectionne_le_bon_seuil():
    cfg = charger_config(CYCLE_LONG)
    assert cfg.palier_pour(100).nom == "prioritaire"
    assert cfg.palier_pour(90).nom == "prioritaire"
    assert cfg.palier_pour(89).nom == "standard"
    assert cfg.palier_pour(0).nom == "veille"


def test_palier_pour_sur_les_paliers_du_cdcf():
    cfg = charger_config(TEST_ARES)
    assert cfg.palier_pour(96).nom == "quasi_parfait"
    assert cfg.palier_pour(92).nom == "tres_forte"
    assert cfg.palier_pour(75).nom == "correcte"
    assert cfg.palier_pour(40).nom == "faible"


# ----- Seuil de confiance ----------------------------------------------------
def test_seuil_de_confiance_est_configurable():
    assert charger_config(CYCLE_LONG).seuil_confiance == 0.85
    assert charger_config(RESTAURATION).seuil_confiance == 0.60
    assert charger_config(INCONNU).seuil_confiance == 0.7  # défaut CDCF
