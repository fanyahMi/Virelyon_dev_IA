"""Tests de la logique pure des niveaux de confiance opérationnelle (§4.14,
contrat §5.5). Aucun appel LLM, aucune clé requise."""
from app.apex.autonomie import decider_action
from app.schemas.apex import DecideActionRequest, DecisionEscalade, Intention, NiveauAutonomie


def _req(**kwargs) -> DecideActionRequest:
    base = dict(
        niveau_autonomie=NiveauAutonomie.supervise,
        intention=Intention.question_produit,
        confiance=0.9,
        decision_escalade=DecisionEscalade.continuer,
    )
    base.update(kwargs)
    return DecideActionRequest(**base)


def test_cloture_demandee_prioritaire_sur_tout():
    res = decider_action(_req(niveau_autonomie=NiveauAutonomie.autonome, cloture_demandee=True))
    assert res.action == "cloturer"


def test_escalade_toujours_respectee_meme_niveau_autonome():
    res = decider_action(
        _req(niveau_autonomie=NiveauAutonomie.autonome, decision_escalade=DecisionEscalade.escalade)
    )
    assert res.action == "escalade"


def test_niveau_supervise_toujours_brouillon():
    res = decider_action(_req(niveau_autonomie=NiveauAutonomie.supervise))
    assert res.action == "brouillon"


def test_niveau_supervise_meme_avec_haute_confiance():
    """Niveau 1 : APEX n'envoie JAMAIS seul, quelle que soit la confiance (§4.14)."""
    res = decider_action(_req(niveau_autonomie=NiveauAutonomie.supervise, confiance=0.99))
    assert res.action == "brouillon"


def test_niveau_semi_autonome_repond_si_question_produit_confiance_elevee():
    res = decider_action(_req(niveau_autonomie=NiveauAutonomie.semi_autonome, confiance=0.9))
    assert res.action == "repondre"


def test_niveau_semi_autonome_brouillon_si_confiance_insuffisante():
    res = decider_action(_req(niveau_autonomie=NiveauAutonomie.semi_autonome, confiance=0.5))
    assert res.action == "brouillon"


def test_niveau_semi_autonome_brouillon_si_reclamation():
    res = decider_action(
        _req(niveau_autonomie=NiveauAutonomie.semi_autonome, intention=Intention.reclamation, confiance=0.95)
    )
    assert res.action == "brouillon"


def test_niveau_autonome_repond_sauf_escalade():
    res = decider_action(
        _req(niveau_autonomie=NiveauAutonomie.autonome, intention=Intention.reclamation, confiance=0.3)
    )
    assert res.action == "repondre"


def test_decision_brouillon_du_module_45_s_applique_meme_en_niveau_autonome():
    res = decider_action(
        _req(niveau_autonomie=NiveauAutonomie.autonome, decision_escalade=DecisionEscalade.brouillon)
    )
    assert res.action == "brouillon"


def test_seuil_confiance_semi_autonome_est_configurable():
    res = decider_action(
        _req(niveau_autonomie=NiveauAutonomie.semi_autonome, confiance=0.75, seuil_confiance_semi_autonome=0.7)
    )
    assert res.action == "repondre"
