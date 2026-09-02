"""Tests du chargement de configuration APEX. Même enjeu que côté ARES
(tests/test_config.py) : deux clients doivent obtenir des comportements
différents avec le même code."""
from uuid import UUID

from app.apex.config import charger_config, config_par_defaut

INCONNU = "99999999-9999-9999-9999-999999999999"
CLIENT_SEMI_AUTONOME = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CLIENT_AUTONOME = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def test_client_inconnu_recoit_un_defaut_prudent():
    cfg = charger_config(INCONNU)
    assert cfg.statut == "configuration_incomplete"
    assert cfg.niveau_autonomie == "supervise"  # jamais autonome par défaut
    assert cfg.canaux_actifs == ["chat_web"]


def test_le_defaut_ne_change_jamais_de_niveau_sans_choix_explicite():
    cfg = config_par_defaut(INCONNU)
    assert cfg.niveau_autonomie == "supervise"


def test_config_semi_autonome_est_chargee():
    cfg = charger_config(CLIENT_SEMI_AUTONOME)
    assert cfg.workspace_id == UUID(CLIENT_SEMI_AUTONOME)
    assert cfg.statut == "actif"
    assert cfg.niveau_autonomie == "semi_autonome"
    assert cfg.ton_de_voix == "amical"
    assert "get_customer_context" in cfg.outils_actifs


def test_deux_clients_ont_des_comportements_differents():
    a, b = charger_config(CLIENT_SEMI_AUTONOME), charger_config(CLIENT_AUTONOME)
    assert a.niveau_autonomie != b.niveau_autonomie
    assert a.ton_de_voix != b.ton_de_voix
    assert a.outils_actifs != b.outils_actifs


def test_plafond_echanges_personnalise_est_dans_les_regles():
    cfg = charger_config(CLIENT_SEMI_AUTONOME)
    regle = next(r for r in cfg.regles_escalade if r.type_regle == "nb_echanges_sans_resolution")
    assert regle.valeur_seuil["seuil"] == 4
