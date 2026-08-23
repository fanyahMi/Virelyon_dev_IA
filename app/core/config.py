"""Configuration du service IA (chargée depuis l'environnement / .env)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Clé API Claude (server-side uniquement, jamais exposée)
    anthropic_api_key: str = ""
    # Secret partagé backend <-> IA (authentification service-à-service)
    internal_api_key: str = "dev-secret-change-me"

    ai_env: str = "development"
    default_max_tokens: int = 1024

    # Clés des sources de prospection (server-side uniquement).
    # Absentes = le sourcing reste utilisable en mode simulation (dry_run).
    apollo_api_key: str = ""
    google_places_api_key: str = ""
    hunter_api_key: str = ""
    # Plafond de coût cumulé par workspace ($) avant blocage (0 = illimité)
    max_cost_per_workspace: float = 0.0

    # --- Choix du fournisseur LLM --------------------------------------------
    # "anthropic" (défaut, CDCF §5.1) ou "openai_compatible" — ce dernier couvre
    # Mistral, OpenAI, Groq, Ollama, OpenRouter, DeepSeek, Together… qui parlent
    # tous le même dialecte /chat/completions.
    llm_provider: str = "anthropic"
    llm_base_url: str = ""          # ex. https://api.mistral.ai/v1
    llm_api_key: str = ""           # clé du fournisseur choisi
    llm_model_fast: str = ""        # remplace le modèle du tier "fast"
    llm_model_reasoning: str = ""   # remplace le modèle du tier "reasoning"
    # Prix $/1M tokens à utiliser pour un modèle absent de MODEL_PRICING.
    # Sans ça, un modèle inconnu coûterait 0 et le plafond ne se déclencherait
    # JAMAIS — un garde-fou silencieusement désactivé.
    llm_prix_entree: float = 0.0
    llm_prix_sortie: float = 0.0


# Table de routage par défaut : capacité logique -> modèle réel (CDCF §5.1).
# Les agents demandent une CAPACITÉ, jamais un modèle : c'est ce qui permet de
# changer de modèle — ou de fournisseur — sans toucher une ligne de logique.
TIER_TO_MODEL = {
    "fast": "claude-haiku-4-5",       # volume / faible latence : classification
    "reasoning": "claude-sonnet-4-6",  # raisonnement : qualification, génération
}

# Prix en $ / 1M tokens : (entrée, sortie). Complété au besoin ; un modèle absent
# retombe sur llm_prix_entree / llm_prix_sortie.
MODEL_PRICING = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
}


class ConfigurationModeleManquante(RuntimeError):
    """Un fournisseur non-Anthropic est choisi sans préciser ses modèles."""


def modele_pour(tier: str) -> str:
    """Modèle réel pour une capacité, en tenant compte des surcharges d'env.

    Les valeurs de `TIER_TO_MODEL` sont des modèles Claude : elles n'ont de sens
    que pour le fournisseur Anthropic. Avec un autre fournisseur, envoyer
    « claude-sonnet-4-6 » à Groq ou Mistral produirait une erreur illisible —
    on exige donc les noms de modèles explicitement.
    """
    settings = get_settings()
    surcharge = {
        "fast": settings.llm_model_fast,
        "reasoning": settings.llm_model_reasoning,
    }.get(tier, "")
    if surcharge:
        return surcharge
    if settings.llm_provider != "anthropic":
        raise ConfigurationModeleManquante(
            f"LLM_PROVIDER={settings.llm_provider} exige de préciser les modèles : "
            f"renseigner LLM_MODEL_FAST et LLM_MODEL_REASONING dans .env."
        )
    return TIER_TO_MODEL[tier]


def prix_pour(modele: str) -> tuple[float, float]:
    """Prix ($/1M) du modèle, ou les valeurs d'environnement en repli."""
    if modele in MODEL_PRICING:
        return MODEL_PRICING[modele]
    settings = get_settings()
    return settings.llm_prix_entree, settings.llm_prix_sortie


@lru_cache
def get_settings() -> Settings:
    return Settings()
