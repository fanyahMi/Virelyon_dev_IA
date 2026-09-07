"""Fixtures de test. Le provider LLM est mocké : aucun appel réseau, aucune clé requise."""
import os

# Environnement de test figé AVANT tout import de l'app.
# Les variables d'environnement priment sur le .env : les tests ne doivent jamais
# dépendre de la configuration locale du développeur (fournisseur LLM, plafonds,
# modèles). Sans ça, brancher Groq dans son .env casse la suite de tests.
os.environ.update(
    INTERNAL_API_KEY="test-key",
    LLM_PROVIDER="anthropic",
    LLM_BASE_URL="",
    LLM_API_KEY="",
    LLM_MODEL_FAST="",
    LLM_MODEL_REASONING="",
    LLM_PRIX_ENTREE="0",
    LLM_PRIX_SORTIE="0",
    MAX_COST_PER_WORKSPACE="0",
    ANTHROPIC_API_KEY="",
    # Clés de sourcing figées elles aussi : sinon brancher une vraie clé Apollo
    # dans son .env fait partir de vrais appels réseau pendant les tests.
    APOLLO_API_KEY="",
    GOOGLE_PLACES_API_KEY="",
    HUNTER_API_KEY="",
    APIFY_API_TOKEN="",
)

import pytest
from fastapi.testclient import TestClient

from app.gateway.cache import response_cache
from app.gateway.provider import get_provider
from app.main import app

HEADERS = {"X-Internal-Key": "test-key"}


@pytest.fixture(autouse=True)
def _clear_cache():
    """Cache vidé avant chaque test → isolation déterministe."""
    response_cache.clear()
    yield


class FakeProvider:
    """Provider factice : renvoie un payload JSON fixe + un usage de tokens fixe."""

    def __init__(self, payload: str) -> None:
        self.payload = payload

    async def generate(self, model, system, user, max_tokens):
        return self.payload, 100, 50


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def use_provider():
    """Injecte un FakeProvider renvoyant `payload`."""
    def _set(payload: str):
        app.dependency_overrides[get_provider] = lambda: FakeProvider(payload)

    yield _set
    app.dependency_overrides.pop(get_provider, None)


class FauxClientHTTP:
    """Client httpx factice : rend une charge JSON, ou lève l'exception fournie.

    `get` et `post` sont tous deux couverts : Hunter interroge son domain-search
    en GET là où Apollo, Places et Discover postent.

    Les réponses sont routées par URL. Tous les connecteurs importent le **même**
    objet `httpx` : remplacer `httpx.AsyncClient` vaut pour tout le monde, on ne
    peut donc pas donner une charge différente « par module ». Router sur l'URL
    est ce qui permet de tester une chaîne découverte → enrichissement.
    """

    def __init__(self, routes=None, exception=None) -> None:
        self.routes = routes or {}
        self.exception = exception
        self.appels: list[tuple[str, str]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def _repondre(self, methode: str, url: str):
        self.appels.append((methode, url))
        if self.exception is not None:
            raise self.exception
        import httpx

        charge = next(
            (c for fragment, c in self.routes.items() if fragment in url),
            # Fragment vide = charge unique, valable pour toutes les URL.
            self.routes.get(""),
        )
        return httpx.Response(
            200, json=charge, request=httpx.Request(methode, url)
        )

    async def post(self, url="", *_a, **_kw):
        return self._repondre("POST", url)

    async def get(self, url="", *_a, **_kw):
        return self._repondre("GET", url)


@pytest.fixture
def faux_http(monkeypatch):
    """Remplace le client httpx des connecteurs, et le registre côté exécuteur.

    `source` désigne le ou les connecteurs qui reçoivent la clé : les autres
    restent sans clé, donc en statut `non_configuree`, ce qui isole la source
    testée. `charge` sert une réponse unique ; `routes` en associe une par
    fragment d'URL, pour enchaîner plusieurs sources dans un même test.
    """
    def _set(
        module: str = "",
        charge=None,
        exception=None,
        cle="cle-de-test",
        source="apollo",
        routes=None,
    ):
        table = dict(routes or {})
        if charge is not None:
            table.setdefault("", charge)
        monkeypatch.setattr(
            "httpx.AsyncClient", lambda *a, **k: FauxClientHTTP(table, exception)
        )
        avec_cle = {source} if isinstance(source, str) else set(source)
        from app.sourcing.apollo import Apollo
        from app.sourcing.hunter import Hunter, HunterDiscover
        from app.sourcing.apify import ApifyLinkedIn
        from app.sourcing.places import Places
        from app.sourcing.site_web import SiteWeb

        classes = (Apollo, Places, HunterDiscover, Hunter, SiteWeb, ApifyLinkedIn)
        registre = {
            c.source: c for c in (k(cle if k.source in avec_cle else "") for k in classes)
        }
        monkeypatch.setattr("app.sourcing.executeur._connecteurs", lambda: registre)

    return _set


@pytest.fixture
def score_payload():
    return {
        "workspace_id": "11111111-1111-1111-1111-111111111111",
        "lead": {
            "nom": "Studio Créa",
            "secteur": "marketing",
            "taille_effectif": 18,
            "role_contact": "fondateur",
            "contact": {"email": "contact@studio.co"},
            "montant_potentiel": 5000,
        },
        "icp": {
            "secteurs_inclus": ["marketing", "conseil"],
            "secteurs_exclus": ["hotellerie"],
            "taille_min": 5,
            "taille_max": 30,
            "roles_cibles": ["fondateur", "decideur"],
        },
    }
