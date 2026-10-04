"""Contrats AURA IA : validation stricte, et 0 ≠ donnée manquante."""
import pytest
from pydantic import ValidationError

from app.aura.schemas import (
    DonneeManquante,
    IndicatorAvailabilityInput,
    IndicatorAvailabilityOutput,
    NarrativeInput,
    NarrativeOutput,
    PerformanceInput,
    PerformanceOutput,
)

WID = "11111111-1111-1111-1111-111111111111"
PERIODE = {"debut": "2026-09-01", "fin": "2026-09-30"}


def _narr(**kw):
    return {"workspace_id": WID, **kw}


# ----- 0 ≠ manquant -----
def test_zero_est_une_vraie_valeur():
    n = NarrativeInput(**_narr(indicateurs_calcules=[{"id": "leads", "libelle": "Leads", "valeur": 0}]))
    assert n.indicateurs_calcules[0].valeur == 0


@pytest.mark.parametrize("val", [None, True, "12", float("nan"), float("inf")])
def test_valeur_refuse_null_bool_texte_nan(val):
    with pytest.raises(ValidationError):
        NarrativeInput(**_narr(indicateurs_calcules=[{"id": "leads", "libelle": "L", "valeur": val}]))


def test_valeur_obligatoire_pas_de_zero_implicite():
    with pytest.raises(ValidationError):
        NarrativeInput(**_narr(indicateurs_calcules=[{"id": "leads", "libelle": "L"}]))


def test_precedente_distingue_zero_manquant_et_absent():
    base = {"id": "leads", "libelle": "L", "valeur": 5}
    zero = NarrativeInput(**_narr(indicateurs_calcules=[{**base, "valeur_precedente": 0}]))
    manq = NarrativeInput(
        **_narr(indicateurs_calcules=[
            {**base, "valeur_precedente": {"donnee_manquante": True, "motif": "pas d'historique"}}
        ])
    )
    absent = NarrativeInput(**_narr(indicateurs_calcules=[base]))
    assert zero.indicateurs_calcules[0].valeur_precedente == 0
    assert isinstance(manq.indicateurs_calcules[0].valeur_precedente, DonneeManquante)
    assert absent.indicateurs_calcules[0].valeur_precedente is None


def test_donnee_manquante_exige_un_motif_et_le_marqueur():
    with pytest.raises(ValidationError):
        DonneeManquante(donnee_manquante=True, motif="")
    with pytest.raises(ValidationError):
        DonneeManquante(donnee_manquante=False, motif="x")


def test_agregat_manquant_explicite_vs_zero():
    p = PerformanceInput(
        workspace_id=WID, periode=PERIODE,
        ares={"leads_generes": 0, "leads_qualifies": {"donnee_manquante": True, "motif": "CRM non connecté"}},
    )
    assert p.ares["leads_generes"] == 0
    assert isinstance(p.ares["leads_qualifies"], DonneeManquante)
    with pytest.raises(ValidationError):
        PerformanceInput(workspace_id=WID, periode=PERIODE, ares={"leads_qualifies": None})


# ----- Narration -----
def test_narrative_defauts_et_extra_interdit():
    n = NarrativeInput(workspace_id=WID)
    assert n.locale == "fr"
    with pytest.raises(ValidationError):
        NarrativeInput(workspace_id=WID, inconnu=1)


def test_narrative_id_en_double_ou_calcule_et_manquant():
    ind = {"id": "ca", "libelle": "CA", "valeur": 10}
    with pytest.raises(ValidationError):
        NarrativeInput(**_narr(indicateurs_calcules=[ind],
                               donnees_manquantes=[{"id": "ca", "libelle": "CA", "motif": "x"}]))


def test_narrative_locale_invalide():
    with pytest.raises(ValidationError):
        NarrativeInput(workspace_id=WID, locale="francais")


def test_narrative_output_valide_et_rejette_incomplet():
    out = NarrativeOutput.model_validate({"resume": "Bon mois.", "analyses": [
        {"indicateur_id": "leads", "commentaire": "Stable.", "tendance": "stable"}]})
    assert out.analyses[0].tendance == "stable"
    with pytest.raises(ValidationError):
        NarrativeOutput.model_validate({"analyses": []})  # résumé absent
    with pytest.raises(ValidationError):
        NarrativeOutput.model_validate({"resume": "x", "analyses": [
            {"indicateur_id": "leads", "commentaire": "x", "tendance": "explose"}]})


# ----- Performance -----
def test_performance_exemple_du_brief():
    p = PerformanceInput(
        workspace_id=WID, periode=PERIODE,
        ares={"leads_generes": 120, "leads_qualifies": 38},
        apex={"conversations": 75, "tickets_resolus": 61},
    )
    assert p.apex["tickets_resolus"] == 61


def test_performance_exige_un_agent_et_une_periode_ordonnee():
    with pytest.raises(ValidationError):
        PerformanceInput(workspace_id=WID, periode=PERIODE)
    with pytest.raises(ValidationError):
        PerformanceInput(workspace_id=WID, periode={"debut": "2026-10-01", "fin": "2026-09-01"},
                         ares={"a": 1})
    with pytest.raises(ValidationError):
        PerformanceInput(workspace_id=WID, periode=PERIODE, ares={})
    with pytest.raises(ValidationError):
        PerformanceInput(workspace_id=WID, periode=PERIODE, ares={"Cle Invalide": 1})


def test_performance_output():
    out = PerformanceOutput.model_validate(
        {"resume": "ok", "ares": {"synthese": "s", "metriques_citees": ["leads_generes"]}}
    )
    assert out.apex is None
    with pytest.raises(ValidationError):
        PerformanceOutput.model_validate({"resume": "ok"})


# ----- Disponibilité -----
def test_disponibilite_entree():
    i = IndicatorAvailabilityInput(
        workspace_id=WID, indicateur_id="marge",
        sources_requises=[{"nom": "facturation", "connectee": False, "champs_requis": ["montant"]}],
    )
    assert i.sources_requises[0].connectee is False
    with pytest.raises(ValidationError):
        IndicatorAvailabilityInput(workspace_id=WID, indicateur_id="marge", sources_requises=[])


def test_disponibilite_sortie_format_strict():
    assert IndicatorAvailabilityOutput(disponible=True, motif=None).model_dump() == {
        "disponible": True, "motif": None}
    assert IndicatorAvailabilityOutput(
        disponible=False, motif="source de facturation non connectée").disponible is False
    with pytest.raises(ValidationError):
        IndicatorAvailabilityOutput(disponible=True, motif="pourquoi")
    with pytest.raises(ValidationError):
        IndicatorAvailabilityOutput(disponible=False)
    with pytest.raises(ValidationError):
        IndicatorAvailabilityOutput(disponible=False, motif="")
    with pytest.raises(ValidationError):
        IndicatorAvailabilityOutput.model_validate({"disponible": True, "motif": None, "extra": 1})
    with pytest.raises(ValidationError):
        IndicatorAvailabilityOutput.model_validate({"disponible": "oui", "motif": None})
