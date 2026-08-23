"""Fournisseurs LLM — le service n'est lié à aucun modèle en particulier.

Le contrat tient en une méthode : `generate(model, system, user, max_tokens)`
qui rend `(texte, tokens_entrée, tokens_sortie)`. Les agents demandent une
CAPACITÉ (`fast` / `reasoning`), jamais un modèle — changer de fournisseur ne
touche donc aucune ligne de logique métier.

Deux implémentations couvrent l'essentiel du marché :
- `AnthropicProvider` — Claude (défaut du CDCF §5.1) ;
- `OpenAICompatibleProvider` — tout ce qui parle `/chat/completions` :
  Mistral, OpenAI, Groq, Ollama, OpenRouter, DeepSeek, Together, LM Studio…

⚠️ Ce qui change en changeant de modèle, ce n'est pas le code : ce sont les
PROMPTS. Les garde-fous (JSON strict, 5 catégories fermées, interdiction de
suggérer un remplacement humain) doivent être revalidés par fournisseur —
`samples/run.sh` sert exactement à ça.

Le provider est injecté via `get_provider` (dépendance FastAPI) pour être
facilement mocké dans les tests — aucun appel réseau réel en test.
"""
from __future__ import annotations

from typing import Protocol

import httpx

from app.core.config import get_settings


class ReponseLLMInvalide(RuntimeError):
    """Le modèle a répondu, mais sa sortie n'est pas exploitable (JSON cassé,
    champ attendu absent). Distinct d'une indisponibilité : réessayer peut marcher."""


class LLMSurcharge(RuntimeError):
    """Le fournisseur limite le débit (429). Réessayer plus tard fonctionnera."""


class LLMIndisponible(RuntimeError):
    """Le fournisseur a répondu en erreur (5xx, authentification, quota épuisé)."""


class LLMNotConfiguredError(RuntimeError):
    """Levée quand aucune clé LLM n'est configurée (→ 503 côté endpoint)."""


class LLMProvider(Protocol):
    async def generate(
        self, model: str, system: str, user: str, max_tokens: int
    ) -> tuple[str, int, int]:
        """Retourne (texte, input_tokens, output_tokens)."""
        ...


class AnthropicProvider:
    """Claude, via le SDK officiel.

    Le paquet `anthropic` est importé ICI et non en tête de module : le service
    doit pouvoir tourner sans lui. C'est ce qui rend Claude optionnel plutôt
    qu'imposé.
    """

    def __init__(self, api_key: str) -> None:
        # Rien de coûteux ni de faillible ici : construire un provider ne doit
        # jamais échouer. Les décisions déterministes (ex. plafond de relance
        # atteint) traversent la dépendance FastAPI sans jamais appeler le LLM —
        # elles échoueraient en 503 si l'échec avait lieu à la construction.
        self._api_key = api_key or ""
        self._client = None

    def _obtenir_client(self):
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as exc:  # pragma: no cover - dépend de l'installation
                raise LLMNotConfiguredError(
                    "LLM_PROVIDER=anthropic demande le paquet « anthropic » "
                    "(pip install anthropic), ou choisir LLM_PROVIDER=openai_compatible."
                ) from exc
            self._client = AsyncAnthropic(api_key=self._api_key or "placeholder")
        return self._client

    async def generate(
        self, model: str, system: str, user: str, max_tokens: int
    ) -> tuple[str, int, int]:
        if not self._api_key:
            raise LLMNotConfiguredError("ANTHROPIC_API_KEY non configurée.")
        resp = await self._obtenir_client().messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        )
        return text, resp.usage.input_tokens, resp.usage.output_tokens


class OpenAICompatibleProvider:
    """Tout fournisseur exposant `/chat/completions` au format OpenAI.

    Le dialecte est identique chez Mistral, Groq, OpenRouter, DeepSeek, Together
    et Ollama : seule l'URL de base change. Implémenté avec `httpx`, déjà présent
    — pas de dépendance supplémentaire.
    """

    def __init__(self, api_key: str, base_url: str) -> None:
        self._api_key = api_key or ""
        self._base_url = (base_url or "").rstrip("/")

    async def generate(
        self, model: str, system: str, user: str, max_tokens: int
    ) -> tuple[str, int, int]:
        if not self._base_url:
            raise LLMNotConfiguredError("LLM_BASE_URL non configurée.")
        # Ollama et LM Studio tournent en local sans clé : on ne l'exige pas.
        entetes = {"Content-Type": "application/json"}
        if self._api_key:
            entetes["Authorization"] = f"Bearer {self._api_key}"

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            reponse = await client.post(
                f"{self._base_url}/chat/completions",
                headers=entetes,
                json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            # Traduction en exceptions typées : un fournisseur ne doit jamais
            # laisser fuir ses erreurs HTTP brutes, sinon changer de fournisseur
            # change aussi les codes que le backend reçoit.
            if reponse.status_code == 429:
                raise LLMSurcharge(
                    "débit limité par le fournisseur — réessayer dans quelques secondes."
                )
            if reponse.status_code >= 400:
                raise LLMIndisponible(
                    f"HTTP {reponse.status_code} — {reponse.text[:160]}"
                )
            data = reponse.json()

        try:
            texte = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ReponseLLMInvalide(f"réponse inattendue du fournisseur : {exc}") from exc

        usage = data.get("usage") or {}
        return texte, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


def construire_provider() -> LLMProvider:
    """Choisit le fournisseur d'après la configuration."""
    settings = get_settings()
    if settings.llm_provider == "openai_compatible":
        return OpenAICompatibleProvider(settings.llm_api_key, settings.llm_base_url)
    return AnthropicProvider(settings.anthropic_api_key)


_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    """Singleton lazy — surchargé par app.dependency_overrides dans les tests."""
    global _provider
    if _provider is None:
        _provider = construire_provider()
    return _provider
