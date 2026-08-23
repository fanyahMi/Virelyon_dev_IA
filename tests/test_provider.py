"""Indépendance vis-à-vis du fournisseur LLM.

Ces tests vérifient qu'on peut changer de modèle — ou de fournisseur — sans
toucher à la logique métier, et sans désactiver silencieusement un garde-fou.
"""
import asyncio

import httpx
import pytest

from app.core.config import (
    ConfigurationModeleManquante,
    get_settings,
    modele_pour,
    prix_pour,
)
from app.gateway.cost_tracker import CostTracker
from app.gateway.provider import (
    AnthropicProvider,
    LLMIndisponible,
    LLMNotConfiguredError,
    LLMSurcharge,
    OpenAICompatibleProvider,
    ReponseLLMInvalide,
    construire_provider,
)


@pytest.fixture(autouse=True)
def _config_neuve():
    """Les settings sont mémorisés : à vider entre deux scénarios."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ----- Choix du fournisseur --------------------------------------------------
def test_anthropic_par_defaut(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert isinstance(construire_provider(), AnthropicProvider)


def test_bascule_vers_un_fournisseur_compatible(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.mistral.ai/v1")
    assert isinstance(construire_provider(), OpenAICompatibleProvider)


# ----- Le modèle est une capacité, jamais une valeur en dur ------------------
def test_les_modeles_par_defaut_suivent_le_cdcf(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_FAST", "")
    monkeypatch.setenv("LLM_MODEL_REASONING", "")
    assert modele_pour("fast") == "claude-haiku-4-5"
    assert modele_pour("reasoning") == "claude-sonnet-4-6"


def test_les_modeles_sont_surchargeables_sans_toucher_au_code(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_FAST", "mistral-small-latest")
    monkeypatch.setenv("LLM_MODEL_REASONING", "mistral-large-latest")
    assert modele_pour("fast") == "mistral-small-latest"
    assert modele_pour("reasoning") == "mistral-large-latest"


# ----- Le plafond de coût ne doit JAMAIS se désactiver en silence -----------
def test_un_modele_inconnu_sans_prix_configure_coute_zero(monkeypatch):
    """Comportement à connaître : c'est le piège que le test suivant ferme."""
    monkeypatch.setenv("LLM_PRIX_ENTREE", "0")
    monkeypatch.setenv("LLM_PRIX_SORTIE", "0")
    assert prix_pour("modele-maison") == (0.0, 0.0)


def test_le_prix_de_repli_garde_le_plafond_actif(monkeypatch):
    monkeypatch.setenv("LLM_PRIX_ENTREE", "0.2")
    monkeypatch.setenv("LLM_PRIX_SORTIE", "0.6")
    assert prix_pour("mistral-large-latest") == (0.2, 0.6)

    tracker = CostTracker()
    cout = tracker.record("ws", "mistral-large-latest", 1_000_000, 1_000_000)
    assert cout == pytest.approx(0.8)
    with pytest.raises(Exception):  # CostLimitExceeded
        tracker.enforce_limit("ws", 0.5)


def test_le_prix_du_catalogue_prime_sur_le_repli(monkeypatch):
    monkeypatch.setenv("LLM_PRIX_ENTREE", "99")
    assert prix_pour("claude-haiku-4-5") == (1.0, 5.0)


# ----- Conformité du fournisseur compatible ---------------------------------
def _faux_httpx(monkeypatch, charge=None, exception=None):
    class Faux:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            if exception:
                raise exception
            return httpx.Response(
                200, json=charge, request=httpx.Request("POST", "http://test")
            )

    monkeypatch.setattr("app.gateway.provider.httpx.AsyncClient", Faux)


def test_provider_compatible_rend_le_contrat_attendu(monkeypatch):
    _faux_httpx(monkeypatch, charge={
        "choices": [{"message": {"content": '{"qualifie": true}'}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 40},
    })
    provider = OpenAICompatibleProvider("cle", "https://api.mistral.ai/v1")
    texte, entree, sortie = asyncio.run(
        provider.generate("mistral-large-latest", "sys", "user", 512)
    )
    assert texte == '{"qualifie": true}'
    assert (entree, sortie) == (120, 40)


def test_provider_compatible_sans_url_est_explicite():
    with pytest.raises(LLMNotConfiguredError):
        asyncio.run(OpenAICompatibleProvider("cle", "").generate("m", "s", "u", 10))


def test_provider_compatible_tolere_l_absence_de_cle(monkeypatch):
    """Ollama et LM Studio tournent en local sans authentification."""
    _faux_httpx(monkeypatch, charge={
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })
    provider = OpenAICompatibleProvider("", "http://localhost:11434/v1")
    texte, _, _ = asyncio.run(provider.generate("llama3", "s", "u", 10))
    assert texte == "ok"


def test_reponse_malformee_est_typee_pas_un_500(monkeypatch):
    _faux_httpx(monkeypatch, charge={"inattendu": True})
    provider = OpenAICompatibleProvider("cle", "https://api.mistral.ai/v1")
    with pytest.raises(ReponseLLMInvalide):
        asyncio.run(provider.generate("m", "s", "u", 10))


def test_usage_absent_ne_fait_pas_tomber_le_suivi_des_couts(monkeypatch):
    """Certains fournisseurs locaux ne rendent pas d'usage : 0 plutôt qu'un crash."""
    _faux_httpx(monkeypatch, charge={"choices": [{"message": {"content": "ok"}}]})
    provider = OpenAICompatibleProvider("", "http://localhost:11434/v1")
    assert asyncio.run(provider.generate("llama3", "s", "u", 10)) == ("ok", 0, 0)


# ----- Les erreurs HTTP du fournisseur ne doivent JAMAIS fuir en 500 --------
def _faux_statut(monkeypatch, code: int, texte: str = "erreur"):
    class Faux:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return httpx.Response(
                code, text=texte, request=httpx.Request("POST", "http://test")
            )

    monkeypatch.setattr("app.gateway.provider.httpx.AsyncClient", Faux)


def test_429_du_fournisseur_devient_une_surcharge_typee(monkeypatch):
    """Sans ça, un débit limité ressort en 500 opaque côté backend."""
    _faux_statut(monkeypatch, 429, "Too Many Requests")
    provider = OpenAICompatibleProvider("cle", "https://api.groq.com/openai/v1")
    with pytest.raises(LLMSurcharge):
        asyncio.run(provider.generate("m", "s", "u", 10))


def test_500_du_fournisseur_devient_une_indisponibilite_typee(monkeypatch):
    _faux_statut(monkeypatch, 503, "upstream down")
    provider = OpenAICompatibleProvider("cle", "https://api.groq.com/openai/v1")
    with pytest.raises(LLMIndisponible):
        asyncio.run(provider.generate("m", "s", "u", 10))


def test_401_du_fournisseur_ne_passe_pas_pour_une_reponse_invalide(monkeypatch):
    _faux_statut(monkeypatch, 401, "bad key")
    provider = OpenAICompatibleProvider("mauvaise-cle", "https://api.groq.com/openai/v1")
    with pytest.raises(LLMIndisponible):
        asyncio.run(provider.generate("m", "s", "u", 10))


# ----- Claude est optionnel, pas imposé --------------------------------------
def test_un_fournisseur_tiers_doit_nommer_ses_modeles(monkeypatch):
    """Sans ça, « claude-sonnet-4-6 » partirait chez Groq — erreur illisible."""
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_MODEL_FAST", "")
    monkeypatch.setenv("LLM_MODEL_REASONING", "")
    with pytest.raises(ConfigurationModeleManquante):
        modele_pour("reasoning")


def test_le_service_importe_sans_le_paquet_anthropic():
    """Le paquet n'est chargé que par AnthropicProvider, jamais à l'import."""
    import app.gateway.provider as module

    assert "anthropic" not in dir(module)
