"""Tests du chargement de configuration client.

Deux enjeux, et ils sont distincts :
- **deux clients** obtiennent des comportements différents avec le même code —
  sans ça, l'Agent Builder ne sert à rien ;
- **deux agents du même client** aussi — sans ça, « plusieurs agents » n'est
  qu'un libellé sur l'écran.
"""
from uuid import UUID

from app.builder.config import (
    PALIERS_PAR_DEFAUT,
    charger_agent,
    charger_workspace,
    config_par_defaut,
    identifiant_agent,
)

TEST_ARES = "11111111-1111-1111-1111-111111111111"
RESTAURATION = "096c84c1-cf60-4c2b-ab87-8132570a59f4"
CYCLE_LONG = "25db28de-c378-4878-a51a-129016ccfc7f"
INCONNU = "99999999-9999-9999-9999-999999999999"


# ----- Client sans configuration ---------------------------------------------
def test_client_inconnu_recoit_une_equipe_par_defaut():
    """Un compte qui vient d'être créé n'est pas une erreur — il a déjà son équipe."""
    ws = charger_workspace(INCONNU)
    assert [a.role for a in ws.agents] == ["prospecteur", "relanceur"]
    for agent in ws.agents:
        assert agent.statut == "configuration_incomplete"
        assert agent.autonomy_level == "supervision"  # jamais d'envoi automatique
        assert agent.canaux_actifs == ["email"]
        assert agent.paliers == PALIERS_PAR_DEFAUT


def test_le_defaut_ne_cible_personne():
    ws = config_par_defaut(INCONNU)
    assert ws.icp.secteurs_inclus == []
    assert ws.icp.taille_min is None


def test_les_agents_par_defaut_gardent_la_meme_identite():
    """Un identifiant tiré au hasard ferait perdre l'agent au frontend."""
    a = charger_workspace(INCONNU).par_role("prospecteur")
    b = charger_workspace(INCONNU).par_role("prospecteur")
    assert a.agent_id == b.agent_id == identifiant_agent(INCONNU, "prospecteur")


def test_deux_workspaces_nont_pas_les_memes_identifiants_dagents():
    assert identifiant_agent(INCONNU, "prospecteur") != identifiant_agent(
        TEST_ARES, "prospecteur"
    )


# ----- Chargement d'une configuration réelle ---------------------------------
def test_config_de_reference_est_chargee():
    ws = charger_workspace(TEST_ARES)
    assert ws.workspace_id == UUID(TEST_ARES)
    assert ws.zone == "Genève"
    assert "conseil" in ws.icp.secteurs_inclus
    assert "hotellerie" in ws.icp.secteurs_exclus

    prospecteur = ws.par_role("prospecteur")
    assert prospecteur.statut == "actif"
    assert prospecteur.ton_de_voix == "professionnel"
    assert prospecteur.objectif_principal  # non vide : alimente les prompts


def test_licp_appartient_au_workspace_pas_a_lagent():
    """Garde-fou CDCF n°2 : une seule source pour l'ICP, partagée par les agents."""
    ws = charger_workspace(TEST_ARES)
    assert not hasattr(ws.par_role("prospecteur"), "icp")
    assert ws.icp.taille_max == 30


def test_paliers_par_defaut_quand_lagent_ne_les_personnalise_pas():
    assert charger_workspace(TEST_ARES).par_role("prospecteur").paliers == PALIERS_PAR_DEFAUT


def test_nom_et_mission_sont_deduits_quand_le_fichier_les_omet():
    """Le relanceur de ce fichier n'a pas de `nom` : il est dérivé du rôle."""
    relanceur = charger_workspace(RESTAURATION).par_role("relanceur")
    assert relanceur.nom == "Relanceur"


# ----- Le point n°1 : deux clients, deux comportements -----------------------
def test_deux_clients_ont_des_comportements_differents():
    a = charger_workspace(TEST_ARES).par_role("prospecteur")
    b = charger_workspace(RESTAURATION).par_role("prospecteur")
    assert a.ton_de_voix != b.ton_de_voix
    assert a.autonomy_level != b.autonomy_level
    assert a.canaux_actifs != b.canaux_actifs
    assert a.seuil_confiance != b.seuil_confiance
    assert set(charger_workspace(TEST_ARES).icp.secteurs_inclus).isdisjoint(
        charger_workspace(RESTAURATION).icp.secteurs_inclus
    )


def test_un_client_peut_cibler_un_marche_hors_icp_de_la_plateforme():
    """Le référentiel garantit la cohérence du vocabulaire, pas le périmètre."""
    assert charger_workspace(RESTAURATION).icp.secteurs_inclus == [
        "restauration",
        "hotellerie",
    ]


# ----- Le point n°2 : deux agents du MÊME client, deux comportements ---------
def test_deux_agents_du_meme_client_different():
    ws = charger_workspace(TEST_ARES)
    prospecteur, relanceur = ws.par_role("prospecteur"), ws.par_role("relanceur")
    assert prospecteur.agent_id != relanceur.agent_id
    assert prospecteur.ton_de_voix != relanceur.ton_de_voix
    assert prospecteur.canaux_actifs != relanceur.canaux_actifs
    assert prospecteur.objectif_principal != relanceur.objectif_principal


def test_les_ponderations_de_score_sont_par_agent():
    """Sur un prospect qui a déjà répondu, c'est l'engagement qui départage."""
    ws = charger_workspace(TEST_ARES)
    assert ws.par_role("prospecteur").scoring_config.poids_engagement == 0.10  # défaut
    assert ws.par_role("relanceur").scoring_config.poids_engagement == 0.40


def test_les_ponderations_restent_aussi_par_client():
    assert charger_workspace(RESTAURATION).par_role(
        "prospecteur"
    ).scoring_config.poids_fit == 0.50
    assert charger_workspace(TEST_ARES).par_role(
        "prospecteur"
    ).scoring_config.poids_fit == 0.40  # défaut


def test_un_agent_peut_etre_actif_et_lautre_non():
    ws = charger_workspace(RESTAURATION)
    assert ws.par_role("prospecteur").statut == "actif"
    assert ws.par_role("relanceur").statut == "inactif"


# ----- Paliers personnalisés : la cadence vient de la config, pas du code ----
def test_paliers_personnalises_portes_par_le_relanceur():
    relanceur = charger_workspace(CYCLE_LONG).par_role("relanceur")
    assert [e.palier.nom for e in relanceur.paliers] == ["prioritaire", "standard", "veille"]
    assert relanceur.palier_pour(95).relances_max == 6
    assert relanceur.palier_pour(95).cadence[-1] == 60  # cycle long


def test_le_prospecteur_du_meme_client_garde_les_paliers_du_cdcf():
    """Deux agents du même client peuvent tenir des cadences différentes."""
    prospecteur = charger_workspace(CYCLE_LONG).par_role("prospecteur")
    assert prospecteur.palier_pour(96).nom == "quasi_parfait"
    assert prospecteur.palier_pour(40).nom == "faible"


def test_palier_pour_selectionne_le_bon_seuil():
    relanceur = charger_workspace(CYCLE_LONG).par_role("relanceur")
    assert relanceur.palier_pour(100).nom == "prioritaire"
    assert relanceur.palier_pour(90).nom == "prioritaire"
    assert relanceur.palier_pour(89).nom == "standard"
    assert relanceur.palier_pour(0).nom == "veille"


def test_palier_pour_sur_les_paliers_du_cdcf():
    agent = charger_workspace(TEST_ARES).par_role("prospecteur")
    assert agent.palier_pour(96).nom == "quasi_parfait"
    assert agent.palier_pour(92).nom == "tres_forte"
    assert agent.palier_pour(75).nom == "correcte"
    assert agent.palier_pour(40).nom == "faible"


# ----- Seuil de confiance ----------------------------------------------------
def test_seuil_de_confiance_est_configurable():
    assert charger_workspace(CYCLE_LONG).par_role("prospecteur").seuil_confiance == 0.85
    assert charger_workspace(RESTAURATION).par_role("prospecteur").seuil_confiance == 0.60
    assert charger_workspace(INCONNU).par_role("prospecteur").seuil_confiance == 0.7  # CDCF


# ----- Isolation : un agent appartient à un workspace, et à un seul ----------
def test_un_agent_dun_autre_client_est_introuvable():
    """Le risque n°1 du projet : agir avec l'agent d'un autre client."""
    etranger = charger_workspace(RESTAURATION).par_role("prospecteur").agent_id
    assert charger_agent(TEST_ARES, etranger) is None


def test_charger_agent_trouve_lagent_de_son_workspace():
    attendu = charger_workspace(TEST_ARES).par_role("relanceur")
    assert charger_agent(TEST_ARES, attendu.agent_id).role == "relanceur"
