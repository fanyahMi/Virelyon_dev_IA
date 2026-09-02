# VIRELYON — Service IA (ARES · APEX)

Service de **décision IA** pour les agents VIRELYON. **Stateless** : il reçoit tout
dans la requête (lead, ICP, config, fragments de la base de connaissances…),
appelle Claude, renvoie une décision en JSON — **il ne touche jamais la base de
données**. C'est le **backend** qui persiste et gère l'isolation multi-tenant. Ce
découplage rend l'équipe DEV IA autonome.

## Lancer en local

**Prérequis :** Python 3.10+ (3.12 recommandé). Vérifier la version :
`python3 --version` (macOS) / `python --version` (Windows).

### 🍎 macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env         # puis renseigner ANTHROPIC_API_KEY + INTERNAL_API_KEY

pytest -q                    # tests (provider LLM mocké, AUCUNE clé requise)
uvicorn app.main:app --reload --port 8080
# → API : http://localhost:8080   ·   docs : http://localhost:8080/docs
```

Raccourcis (si `make` est installé) : `make install` · `make test` · `make run`.

### 🪟 Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env       # puis renseigner ANTHROPIC_API_KEY + INTERNAL_API_KEY

pytest -q
uvicorn app.main:app --reload --port 8080
# → API : http://localhost:8080   ·   docs : http://localhost:8080/docs
```

> Si PowerShell bloque l'activation (`Activate.ps1`) :
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` puis réessayer.
> En invite de commandes (CMD) : `.venv\Scripts\activate.bat`.

### 🐳 Docker (macOS + Windows, identique)

```bash
docker compose up -d --build
# → http://localhost:8080/docs      (logs : docker compose logs -f ai)
```

## Tester ARES avec les données d'exemple

Le service doit tourner (voir ci-dessus).

- **macOS / Linux :**
  ```bash
  ./samples/run.sh
  ```
- **Windows :** le script est en Bash → l'utiliser via **Git Bash** ou **WSL**,
  ou appeler les endpoints directement avec `curl` (inclus dans Windows 10+) :
  ```powershell
  curl -X POST http://localhost:8080/api/v1/ares/score `
    -H "X-Internal-Key: demo-secret" -H "Content-Type: application/json" `
    --data "@samples/score_bon.json"
  ```

Détail des jeux de données : voir `samples/README.md`.

## Sécurité

- **Authentification service-à-service** : tous les endpoints métier exigent le header
  `X-Internal-Key: <INTERNAL_API_KEY>` (secret partagé avec le backend, comparé en temps constant).
- **Pas de CORS** : jamais appelé par un navigateur, uniquement server-à-server.
- **Isolation réseau** (à faire au déploiement) : ne pas exposer le port publiquement —
  accessible uniquement par le backend (réseau interne / Security Group).
- **Clé Claude** server-side uniquement, jamais renvoyée.
- **Plafond de coût par workspace** (`MAX_COST_PER_WORKSPACE`) pour protéger la facture LLM.

## Contrat d'API (ce que le backend appelle)

Toutes les routes `/api/v1/...` exigent `X-Internal-Key`. `workspace_id` obligatoire.
Détail complet (entrées/sorties, exemples) : **`docs/CONTRAT_API.md`**.

### ARES — prospection

| Endpoint | Tier | Entrée | Sortie |
|---|---|---|---|
| `POST /api/v1/ares/qualify` | reasoning (Sonnet) | `{workspace_id, lead, icp}` | `{qualifie, confiance, motif, meta}` |
| `POST /api/v1/ares/score` | — (logique pure) | `{workspace_id, lead, icp, scoring_config?}` | `{score, breakdown, palier}` |
| `POST /api/v1/ares/generate` | reasoning (Sonnet) | `{workspace_id, lead, etape, ton_de_voix, historique, language}` | `{texte, canal, meta}` |
| `POST /api/v1/ares/classify` | fast (Haiku) | `{workspace_id, message_entrant, language}` | `{categorie, confiance, date_relance?, meta}` |
| `POST /api/v1/ares/decide` | déterministe + reasoning | `{workspace_id, lead, palier, relances_effectuees, contexte?}` | `{action, justification, meta?}` |

### APEX — support client (CDCF APEX v2.0, voir `docs/APEX.md`)

| Endpoint | Tier | Entrée | Sortie |
|---|---|---|---|
| `POST /api/v1/apex/classify` | fast (Haiku) | `{workspace_id, message_entrant, historique, fragments_candidats, seuil_pertinence}` | `{intention, fragments_pertinents, confiance, langue_detectee, necessite_escalade, meta}` |
| `POST /api/v1/apex/generate` | reasoning (Sonnet) — inclut la sélection d'outil | `{workspace_id, fragments_pertinents, historique, ton_de_voix, langue, outils_actifs, resultat_outil?, seuil_pertinence}` | `{texte, confiance, justification, necessite_escalade, appel_outil_demande, meta}` |
| `POST /api/v1/apex/escalade` | déterministe + reasoning | `{workspace_id, message_entrant, historique, intention?, regles_escalade, nb_echanges_sans_resolution}` | `{sentiment, declencheurs_actifs, tentative_contournement, decision, justification, meta}` |
| `POST /api/v1/apex/decide-action` | — (logique pure) | `{niveau_autonomie, intention, confiance, decision_escalade, cloture_demandee}` | `{action, justification}` |
| `GET /api/v1/apex/config/{workspace_id}` | — | — | `ConfigAgentApex` (niveau d'autonomie, ton de voix, outils actifs…) |

### Transverse

| Endpoint | Entrée | Sortie |
|---|---|---|
| `GET /api/v1/costs/{workspace_id}` | — | `{input_tokens, output_tokens, cost}` |
| `GET /health` | — (public) | `{status, service}` |

Le **Swagger** (`/docs`) est le contrat vivant : partagez-le au backend, chacun code contre.

### Exemple
```bash
curl -X POST http://localhost:8080/api/v1/ares/score \
  -H "X-Internal-Key: $INTERNAL_API_KEY" -H "Content-Type: application/json" \
  -d '{"workspace_id":"11111111-1111-1111-1111-111111111111",
       "lead":{"nom":"Studio Créa","secteur":"marketing","taille_effectif":18,"role_contact":"fondateur"},
       "icp":{"secteurs_inclus":["marketing"],"taille_min":5,"taille_max":30,"roles_cibles":["fondateur"]}}'
```

## Structure
```
app/
├── core/       config, sécurité (auth service-à-service)
├── gateway/    provider Claude, routage tier→modèle, cost_tracker
├── ares/       scoring (pur) + agents (qualify/generate/classify/decide via Claude)
├── apex/       agents (classify/generate/escalade via Claude) + autonomie (pur, §4.14) + config
├── builder/    paramétrage client (ICP, plan de recherche)
├── sourcing/   connecteurs externes (Apollo, Google Places, Hunter, LinkedIn, site web)
├── prompts/    prompts système (garde-fous CDCF §0 / §4.13)
├── schemas/    contrats Pydantic (I/O)
└── api/v1/     endpoints (ares, apex, builder, sourcing, costs, health)
tests/          pytest (provider mocké — pas d'appel réseau)
```

## Modèles Claude (CDCF §5.1)
- `fast` → **Claude Haiku 4.5** (`claude-haiku-4-5`) : classification.
- `reasoning` → **Claude Sonnet 4.6** (`claude-sonnet-4-6`) : qualification, génération, décision.

## Garde-fous non-négociables (CDCF §0 / CDCF APEX §0, §4.13)
Jamais suggérer de « remplacer » un humain · sorties toujours structurées (JSON) ·
confiance faible ou ambiguë → escalade/file manuelle, jamais tranché au hasard ·
contenu externe (chunks, réponses de prospects) traité comme donnée, jamais comme
instruction · l'ICP vient du backend (jamais stocké ici) · **règle absolue :
APEX ne répond JAMAIS depuis sa connaissance générale — sans fragment pertinent
au-dessus du seuil configuré, aucun appel LLM n'est fait et une escalade est
requise ; vérifié EN PYTHON dans `/apex/classify` et `/apex/generate`, pas
seulement dans le prompt (voir `app/apex/agents.py`).**
