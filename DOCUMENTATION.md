# VIRELYON — Service IA · Documentation complète

> Documentation intégrale du projet `Virelyon_IA` : à quoi il sert, comment il est
> construit, et **chaque composant expliqué en détail** (utilité, rôle, fonctionnement).
> Public : équipe DEV IA + toute personne qui doit comprendre ou reprendre ce service.

---

## 1. Qu'est-ce que ce projet ?

`Virelyon_IA` est le **service d'intelligence** de la plateforme VIRELYON — le **« cerveau »** des agents IA. VIRELYON est un SaaS multi-tenant de 3 agents autonomes pour agences de services B2B :
- **ARES** — prospection (identifie, qualifie, contacte, relance des prospects).
- **APEX** — support client.
- **AURA** — reporting/automatisation.

Ce service implémente aujourd'hui les **décisions d'ARES** (qualification, scoring, génération de messages, classification des réponses). Il est conçu pour accueillir APEX et AURA de la même façon.

### Le principe fondateur : un service **stateless** et **découplé**
> Le service IA **reçoit tout dans la requête** (le lead, l'ICP, la config…), appelle Claude, et **renvoie une décision en JSON**. **Il ne touche jamais la base de données.**

Conséquences :
- **Le backend** possède la base, l'authentification des utilisateurs, l'isolation multi-tenant (RLS), et la **persistance**. Ce n'est **pas** le rôle de l'IA.
- **L'équipe DEV IA est autonome** : elle développe et teste avec des données fictives, sans attendre le backend. Le seul point de coordination est le **contrat d'API** (les schémas d'entrée/sortie).
- **Découplage total** : peu importe la techno du backend (Python, Node, Supabase…), il appelle ce service en HTTP.

Cela reflète la séparation des rôles du CDCF : **Big Data fournit la matière**, **l'IA fournit le jugement**, **le backend expose et persiste**.

---

## 2. Vue d'ensemble de l'architecture interne

Le service est organisé en **couches**, de l'extérieur (HTTP) vers l'intérieur (logique + LLM) :

```
        Requête HTTP (le backend appelle)
                    │
                    ▼
   ┌──────────────────────────────────────┐
   │ app/main.py         (montage FastAPI) │
   │ app/api/v1/         (ENDPOINTS)       │  ← reçoit, valide, protège (auth)
   └───────────────┬──────────────────────┘
                   ▼
   ┌──────────────────────────────────────┐
   │ app/ares/           (LOGIQUE AGENT)   │  ← scoring pur + orchestration des appels
   └───────────────┬──────────────────────┘
                   ▼
   ┌──────────────────────────────────────┐
   │ app/gateway/        (ACCÈS LLM)       │  ← routage tier→modèle, appel Claude, coûts
   └───────────────┬──────────────────────┘
                   ▼
             API Claude (Anthropic)

   Transverses : app/core (config, sécurité) · app/schemas (contrats) · app/prompts (prompts)
```

**Deux types d'endpoints :**
- **Logique pure** (`/score`) — pas d'appel LLM, calcul déterministe → rapide, testable sans clé.
- **Décision LLM** (`/qualify`, `/generate`, `/classify`) — appelle Claude via la gateway.

---

## 3. Arborescence du projet

```
Virelyon_IA/
├── app/
│   ├── main.py                  # point d'entrée FastAPI (monte tout)
│   ├── core/
│   │   ├── config.py            # configuration + table modèles + prix
│   │   └── security.py          # authentification service-à-service
│   ├── gateway/
│   │   ├── provider.py          # fournisseur LLM (Anthropic | OpenAI-compatible)
│   │   ├── router.py            # passerelle : tier→modèle, appel, extraction JSON
│   │   ├── cache.py             # cache mémoire des réponses (coût 0 sur doublon)
│   │   └── cost_tracker.py      # suivi des coûts par workspace ET par agent
│   ├── ares/
│   │   ├── scoring.py           # scoring & paliers de relance (logique PURE)
│   │   └── agents.py            # qualify / generate / classify / decide (via LLM)
│   ├── builder/
│   │   ├── referentiels.py      # vocabulaire canonique (secteurs, rôles, pays…)
│   │   ├── icp.py               # validation (pure) + extraction d'ICP (LLM)
│   │   ├── plan_recherche.py    # ICP → requêtes par source (pur, déterministe)
│   │   ├── roles.py             # rôles d'agent et capacités associées
│   │   ├── habilitation.py      # cet agent a-t-il le droit de faire cet appel ?
│   │   └── config.py            # configuration du client et de ses agents
│   ├── sourcing/
│   │   ├── base.py              # interface des connecteurs + construction de Lead
│   │   ├── executeur.py         # découverte → filtrage ICP → enrichissement
│   │   ├── apify.py            # LinkedIn via l'acteur Apify (réserve juridique)
│   │   └── apollo.py · hunter.py · places.py · site_web.py
│   ├── prompts/
│   │   ├── ares.py              # prompts système (garde-fous non négociables)
│   │   └── builder.py           # prompt d'extraction d'ICP
│   ├── schemas/
│   │   ├── common.py            # socle des requêtes (RequeteAgent) + métadonnées
│   │   ├── ares.py              # contrats d'entrée/sortie (Lead, ICP, requêtes…)
│   │   ├── builder.py           # référentiels, diagnostics, plan de recherche
│   │   ├── config.py            # ConfigWorkspace / ConfigAgent
│   │   └── sourcing.py          # exécution d'un plan, résultats par source
│   └── api/v1/
│       ├── router.py            # regroupe les endpoints /api/v1
│       └── endpoints/
│           ├── health.py        # GET /health (public)
│           ├── ares.py          # POST /ares/{qualify,score,generate,classify,decide}
│           ├── builder.py       # référentiels, ICP, plan de recherche, config
│           ├── sourcing.py      # POST /sourcing/executer
│           └── costs.py         # GET /costs/{workspace_id}
├── tests/                       # pytest (provider LLM mocké — pas de réseau)
├── requirements.txt             # dépendances Python
├── Dockerfile                   # image (Python 3.12, non-root, healthcheck)
├── docker-compose.yml           # lancement dev
├── Makefile                     # install / test / run / docker
├── .env.example                 # variables d'environnement
└── README.md                    # démarrage rapide
```

---

## 4. Détail de chaque composant (« les services existants »)

### 4.1 `app/main.py` — Le point d'entrée
**Utilité :** construit l'application FastAPI et **monte** tous les routeurs.
- Expose `/health` (public) et `/api/v1/...` (métier, protégés).
- **Volontairement : aucun middleware CORS** — le service n'est jamais appelé par un navigateur, uniquement server-à-server. C'est une décision de sécurité.
- Fournit `/docs` (Swagger auto-généré) = le **contrat vivant** à partager au backend.

### 4.2 `app/core/config.py` — La configuration
**Utilité :** centralise tous les réglages, lus depuis l'environnement / `.env` (via `pydantic-settings`).
- `anthropic_api_key` — la clé Claude (server-side uniquement).
- `internal_api_key` — le **secret partagé** backend↔IA (authentification).
- `default_max_tokens`, `max_cost_per_workspace` — réglages LLM et garde-fou coût.
- **`TIER_TO_MODEL`** — la table de routage : `fast → claude-haiku-4-5`, `reasoning → claude-sonnet-4-6` (CDCF §5.1). C'est **le seul endroit** où les modèles réels sont nommés.
- **`MODEL_PRICING`** — prix par modèle ($/1M tokens) pour le calcul des coûts.
- `get_settings()` est mis en cache (`lru_cache`) → lu une fois.

### 4.3 `app/core/security.py` — L'authentification service-à-service
**Utilité :** garantir que **seul le backend** (qui détient le secret) peut appeler le service.
- `verify_caller` est une **dépendance FastAPI** : elle lit le header `X-Internal-Key` et le compare au secret attendu avec **`hmac.compare_digest`** (comparaison en temps constant → protège contre les attaques temporelles).
- Sans header ou mauvais secret → **401**.
- Appliquée à **tous** les endpoints métier (`/ares`, `/costs`), pas à `/health`.

### 4.4 `app/gateway/provider.py` — Le fournisseur LLM
**Utilité :** parler concrètement à Claude, tout en restant **remplaçable/mockable**.
- `LLMProvider` (Protocol) : l'interface commune `generate(model, system, user, max_tokens) → (texte, tokens_in, tokens_out)`.
- `AnthropicProvider` : l'implémentation réelle avec le SDK **`anthropic`** (`AsyncAnthropic`, client asynchrone).
- `LLMNotConfiguredError` : levée si aucune clé n'est configurée → l'endpoint renvoie un **503 clair** au lieu d'un crash.
- `get_provider()` : renvoie le provider (singleton). **Point d'injection clé** : dans les tests, on le remplace par un `FakeProvider` → aucun appel réseau, aucune clé requise.

### 4.5 `app/gateway/router.py` — La passerelle IA (le cœur)
**Utilité :** orchestrer un appel LLM de bout en bout, de façon homogène pour tous les agents.
- `Gateway.complete_json(tier, system, user, workspace_id)` :
  1. traduit le **tier** (`fast`/`reasoning`) en **modèle réel** (via `TIER_TO_MODEL`),
  2. appelle le provider,
  3. enregistre le **coût** (par workspace),
  4. **extrait le JSON** de la réponse (`_extract_json`, tolère les ``` et le texte autour),
  5. renvoie `(données, métadonnées)`.
- `get_gateway()` : dépendance FastAPI qui construit une `Gateway` autour du provider courant.
- **Principe :** un agent demande une **capacité** (tier), jamais un modèle → on peut changer de modèle sans toucher aux agents.

### 4.6 `app/gateway/cost_tracker.py` — Le suivi des coûts
**Utilité :** connaître le coût LLM **par workspace** (donnée source pour FINANCE, marge cible 85-90 %, CDCF §5.4).
- `compute_cost(model, in, out)` — applique la grille `MODEL_PRICING`.
- `record(...)` — agrège tokens + coût par `workspace_id` (thread-safe).
- `get(workspace_id)` — consulté par l'endpoint `/costs`.
- Stockage **en mémoire** (indépendant du backend) — remis à zéro au redémarrage ; à brancher sur Redis/DB pour une persistance longue.

### 4.7 `app/ares/scoring.py` — Le scoring (logique PURE)
**Utilité :** ordonner les leads et piloter les relances, **sans appel LLM** (déterministe, testable, gratuit).
- Calcule un **score composite 0-100** à partir de 4 composantes (CDCF §4.3) :
  - `fraicheur` — ancienneté du lead (`ingested_at`).
  - `completude` — champs renseignés.
  - `fit` — correspondance avec l'ICP (**secteur exclu → fit = 0**, hors-ICP).
  - `engagement` — nombre de signaux détectés.
- Pondérations **configurables par workspace** (`scoring_config`), jamais figées.
- Détermine le **palier de relance** (CDCF §4.3.1) : `≥95 %→5 relances`, `90-94→4`, `70-89→3`, `<70→1`, avec la cadence (J0, J+3, …).

### 4.8 `app/ares/agents.py` — Les décisions via Claude
**Utilité :** les 3 points d'appel LLM d'ARES. Chaque fonction : construit l'entrée JSON, appelle le bon tier, valide la sortie, joint les métadonnées (modèle, tokens, coût).
- `qualify` (tier **reasoning**/Sonnet, prompt §5.1) → `{qualifie, confiance, motif}`.
- `generate` (tier **reasoning**/Sonnet, prompt §5.2) → `{texte, canal}` (+ garde-fou anti-remplacement).
- `classify` (tier **fast**/Haiku, prompt §5.3) → `{categorie, confiance, date_relance?}`.

### 4.9 `app/prompts/ares.py` — Les prompts système
**Utilité :** définir le comportement de Claude et **imposer les garde-fous non négociables** (CDCF §0).
- Chaque prompt force une **sortie JSON stricte**.
- `GENERATE_SYSTEM` contient le garde-fou : **ne jamais suggérer qu'ARES remplace un humain** (« augmenter », pas « remplacer »).
- `PROMPT_VERSION` — versionne les prompts (traçabilité).
- Le texte définitif reste à valider avec DESIGN ; **les contrats (JSON) sont figés**.

### 4.10 `app/schemas/` — Les contrats (Pydantic)
**Utilité :** définir et **valider** les entrées/sorties de l'API. C'est le **contrat** avec le backend.
- `common.py` : `Usage` (tokens) et `Meta` (modèle, usage, coût) joints aux réponses LLM.
- `ares.py` : `Lead`, `ICP`, `ScoringConfig`, `Palier`, et les requêtes/réponses (`QualifyRequest/Response`, `ScoreRequest/Response`, etc.).
- Pydantic **rejette automatiquement** tout payload malformé (défense en entrée) et alimente le Swagger.

### 4.11 `app/api/v1/endpoints/` — Les endpoints (la surface HTTP)
- `health.py` — `GET /health` **public** (pour les healthchecks Docker/infra).
- `ares.py` — les 4 endpoints ARES, **tous protégés** par `verify_caller`. Contient `_llm_guard` qui convertit les erreurs LLM en réponses **propres** : `503` (LLM non configuré / indisponible), `502` (réponse LLM invalide) — jamais de 500 opaque.
- `costs.py` — `GET /costs/{workspace_id}` (protégé) pour FINANCE.
- `router.py` — regroupe `ares` + `costs` sous `/api/v1`.

### 4.12 `tests/` — Les tests
**Utilité :** garantir que tout marche **sans appel réseau ni clé** (le provider est mocké).
- `conftest.py` — fixtures : client de test, `FakeProvider`, payloads.
- `test_health.py` — l'endpoint public.
- `test_auth.py` — **sécurité** : 401 sans clé / mauvaise clé, 200 avec la bonne.
- `test_scoring.py` — logique pure (lead dans/hors ICP, bornes, endpoint).
- `test_agents.py` — qualify/generate/classify avec provider mocké + gestion d'une réponse LLM invalide (502).
- `test_costs.py` — agrégation des coûts par workspace + protection.
- **14 tests, tous verts.**

### 4.13 Fichiers d'infrastructure
- `requirements.txt` — dépendances (FastAPI, uvicorn, anthropic, pydantic, pytest…).
- `Dockerfile` — image Python 3.12-slim, **utilisateur non-root**, **healthcheck** sur `/health`.
- `docker-compose.yml` — lancement dev (⚠️ le port n'est publié qu'en dev ; en prod le service reste interne).
- `Makefile` — `install` / `test` / `run` / `docker`.
- `.env.example` — variables (clé Claude, secret interne, plafond de coût).

---


## 4 bis. Le modèle d'agents (`builder/roles.py`, `builder/habilitation.py`)

**Utilité :** un client n'a pas *un* ARES mais une **équipe d'agents nommés**. Sans ce
modèle, « plusieurs agents » ne serait qu'un libellé sur l'écran — n'importe lequel
pourrait exécuter n'importe quel appel.

- `roles.py` — le registre : deux rôles (`prospecteur`, `relanceur`) et les capacités
  de chacun. Ajouter un rôle se fait **ici, et nulle part ailleurs**.
- `habilitation.py` — trois questions, dans cet ordre : l'agent appartient-il à ce
  workspace (**404**), est-il activé (**409**), son poste autorise-t-il cet acte (**403**) ?
- `schemas/config.py` — la frontière qui compte : l'**ICP appartient au workspace**
  (source unique, CDCF §0 garde-fou n°2), la **manière de travailler appartient à
  l'agent** (mission, voix, canaux, pondérations, paliers, autonomie).

`agent_id` reste **facultatif** dans les requêtes : sans lui, le comportement d'origine
est conservé. Avec lui, la configuration de l'agent **l'emporte** sur ce que joint
l'appelant — sinon une requête pourrait redéfinir le ton au coup par coup et rouvrir
la dérive que l'Agent Builder existe pour empêcher.

> Ce qui **n'existe pas** : rien ne déclenche ces agents. Le service reste réactif —
> il répond quand on l'appelle. Le moteur qui les fait travailler (n8n ou un
> ordonnanceur côté backend) est hors de ce dépôt.

---

## 5. Le contrat d'API (ce que le backend appelle)

Toutes les routes `/api/v1/...` exigent le header **`X-Internal-Key`** et un **`workspace_id`**.

| Endpoint | Type | Entrée | Sortie |
|---|---|---|---|
| `POST /api/v1/ares/qualify` | LLM (Sonnet) | `{workspace_id, lead, icp}` | `{qualifie, confiance, motif, meta}` |
| `POST /api/v1/ares/score` | pur | `{workspace_id, lead, icp, scoring_config?}` | `{score, breakdown, palier}` |
| `POST /api/v1/ares/generate` | LLM (Sonnet) | `{workspace_id, lead, etape, ton_de_voix, historique, language}` | `{texte, canal, meta}` |
| `POST /api/v1/ares/classify` | LLM (Haiku) | `{workspace_id, message_entrant, language}` | `{categorie, confiance, date_relance?, meta}` |
| `GET /api/v1/costs/{workspace_id}` | — | — | `{input_tokens, output_tokens, cost}` |
| `GET /health` | public | — | `{status, service}` |

Le **Swagger `/docs`** est la source de vérité vivante du contrat.

### Exemple de flux d'un appel `/qualify`
```
Backend → POST /api/v1/ares/qualify  (X-Internal-Key + {workspace_id, lead, icp})
  → endpoints/ares.py     : vérifie l'auth (verify_caller), valide le payload (Pydantic)
  → ares/agents.qualify   : construit le JSON d'entrée
  → gateway.complete_json : tier "reasoning" → claude-sonnet-4-6 → appel Claude
  → cost_tracker.record   : enregistre le coût pour ce workspace
  → extraction JSON        : {qualifie, confiance, motif}
  ← réponse {qualifie, confiance, motif, meta:{model_used, usage, cost_estimate}}
Backend → persiste le résultat + écrit un lead_event (côté backend, RLS)
```

---

## 6. Sécurité (en détail)

| Mesure | Où | Pourquoi |
|---|---|---|
| **Auth service-à-service** (`X-Internal-Key`, `hmac.compare_digest`) | `core/security.py` | Seul le backend peut appeler ; anti timing-attack |
| **Pas de CORS** | `main.py` | Jamais appelé par un navigateur (server-à-server) |
| **Clé Claude server-side** | `config.py` / env | Jamais exposée ni renvoyée |
| **Validation stricte des entrées** | `schemas/` (Pydantic) | Rejette les payloads malformés |
| **Erreurs LLM propres** (503/502) | `endpoints/ares.py` | Pas de 500 opaque ; pas de fuite d'infos |
| **Suivi + plafond de coût par workspace** | `cost_tracker.py` / config | Protège la facture LLM |
| **Isolation réseau** (à faire au déploiement) | infra | Port non exposé publiquement — seul le backend y accède |
| **Utilisateur non-root** | `Dockerfile` | Réduit la surface d'attaque |

**Défense en profondeur :** réseau isolé **+** secret partagé → même si l'un cède, l'autre protège.

---

## 7. Modèles Claude & routage

| Tier (capacité demandée) | Modèle réel | Usage |
|---|---|---|
| `fast` | `claude-haiku-4-5` (1 $/5 $ par 1M tokens) | classification (volume, latence) |
| `reasoning` | `claude-sonnet-4-6` (3 $/15 $ par 1M tokens) | qualification, génération (raisonnement) |

L'agent ne demande jamais un modèle, seulement un tier → changement de modèle sans impact sur le code des agents.

---

## 8. Comment lancer & tester

```bash
make install            # crée .venv + installe les dépendances
cp .env.example .env    # renseigner ANTHROPIC_API_KEY + INTERNAL_API_KEY
make test               # 14 tests (provider mocké, aucune clé requise)
make run                # http://localhost:8080  (docs: /docs)
# ou : make docker      # docker compose up -d --build
```

**Note environnement :** le `python3` par défaut du Mac est en **3.9** ; le code reste compatible (via `from __future__ import annotations` là où nécessaire), et l'image **Docker est en 3.12**. Pour un dev local idéal, installer Python 3.12.

---

## 9. Décisions de conception (le « pourquoi »)

- **Stateless** → autonomie de l'équipe + découplage total du backend.
- **Une gateway unique** → un seul endroit qui parle à Claude (clés, routage, coûts, robustesse).
- **Tier au lieu de modèle** → flexibilité de modèle sans refactor.
- **Scoring en logique pure** → rapide, déterministe, testable sans réseau (et gratuit).
- **Sorties JSON structurées** → contrat fiable pour le backend (CDCF §5).
- **Provider injectable** → tests offline, sans clé, sans coût.
- **Garde-fous dans les prompts** → critères de recette (CDCF §0), pas des préférences.

---

## 10. Ce que ce service NE fait PAS (frontières)

- ❌ Pas d'accès à la base de données (le backend possède la donnée).
- ❌ Pas d'authentification des utilisateurs finaux (c'est le backend ; ici, auth **service-à-service** seulement).
- ❌ Pas d'isolation multi-tenant des données / RLS (backend).
- ❌ Pas d'orchestration / relances / envois (c'est **n8n**).
- ❌ Pas d'ingestion / enrichissement de données (c'est **Big Data**).
- ❌ Pas d'agrégats de reporting (l'IA produit des décisions ; AURA/Big Data agrègent).

---

## 11. Évolutions prévues

- `POST /ares/decide` (prompt §5.4 : action continuer/pause/escalade/arrêt).
- Activation du **plafond de coût** (blocage au-delà du seuil par workspace).
- Mémoire vectorielle (**RAG pgvector**, §4.9) pour re-contextualiser les messages.
- Réplication du pattern pour **APEX** (support) et **AURA** (analyses).

---

## 12. Références
- CDCF ARES v1.1 (`../Virelyon_Front_et_Back/CDCF_ARES_...`) — spécification fonctionnelle des modules.
- `../Virelyon_Front_et_Back/BRIQUE_3_IA.md` — la brique IA dans l'architecture globale.
- `../Virelyon_Front_et_Back/AI_GATEWAY_TECH.md` — détails techniques du gateway (SDK Claude).
- `README.md` — démarrage rapide.
