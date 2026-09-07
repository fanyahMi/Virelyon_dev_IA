# VIRELYON — Service IA (ARES)

Service de **décision IA** pour les agents VIRELYON. **Stateless** : il reçoit tout
dans la requête (lead, ICP, config), appelle Claude, renvoie une décision en JSON —
**il ne touche jamais la base de données**. C'est le **backend** qui persiste et gère
l'isolation multi-tenant. Ce découplage rend l'équipe DEV IA autonome.

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

## Les agents d'un client

Un workspace n'a pas *un* ARES mais une **équipe d'agents nommés**, chacun tenant un
poste. Le `agent_id` se joint à la requête ; le service vérifie alors qu'il appartient
bien au workspace, qu'il est activé et que son rôle autorise l'acte demandé.

| Rôle | Métier | Capacités |
|---|---|---|
| `prospecteur` | trouver, trier, engager le premier contact | `sourcing` · `qualify` · `score` · `generate` |
| `relanceur` | entretenir la conversation jusqu'à l'escalade humaine | `score` · `generate` · `classify` · `decide` |

Nommer un agent n'est **pas obligatoire** : sans `agent_id`, l'appel se comporte comme
avant. Avec, **sa configuration l'emporte** sur ce que joint l'appelant — mission, ton,
canaux, pondérations, paliers. Deux agents du même client notent donc le même lead
différemment, et écrivent avec une voix différente.

L'**ICP reste au niveau du workspace** : source unique partagée par tous les agents
(CDCF §0, garde-fou n°2). Seule la manière de travailler appartient à l'agent.

## Contrat d'API (ce que le backend appelle)

Toutes les routes `/api/v1/...` exigent `X-Internal-Key`. `workspace_id` obligatoire,
`agent_id` facultatif.

| Endpoint | Tier | Entrée | Sortie |
|---|---|---|---|
| `POST /api/v1/ares/qualify` | reasoning (Sonnet) | `{workspace_id, agent_id?, lead, icp}` | `{qualifie, confiance, motif, meta}` |
| `POST /api/v1/ares/score` | — (logique pure) | `{workspace_id, agent_id?, lead, icp, scoring_config?}` | `{score, breakdown, palier}` |
| `POST /api/v1/ares/generate` | reasoning (Sonnet) | `{workspace_id, agent_id?, lead, etape, historique, language}` | `{texte, canal, meta}` |
| `POST /api/v1/ares/classify` | fast (Haiku) | `{workspace_id, agent_id?, message_entrant, language}` | `{categorie, confiance, date_relance?, meta}` |
| `POST /api/v1/ares/decide` | reasoning (Sonnet) | `{workspace_id, agent_id?, lead, palier, relances_effectuees}` | `{action, justification, meta?}` |
| `GET /api/v1/builder/config/{workspace_id}` | — | — | `{icp, zone, agents: [...]}` |
| `GET /api/v1/costs/{workspace_id}` | — | — | `{input_tokens, output_tokens, cost, par_agent}` |
| `GET /health` | — (public) | — | `{status, service}` |

Codes propres à l'habilitation : **403** l'agent n'a pas cette capacité · **404** agent
inconnu dans ce workspace · **409** agent non activé par le client.

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
├── gateway/    provider Claude, routage tier→modèle, cost_tracker (par agent)
├── ares/       scoring (pur) + agents (qualify/generate/classify/decide via Claude)
├── builder/    référentiels, ICP, plan de recherche, rôles & habilitation des agents
├── sourcing/   connecteurs vers les sources externes (Hunter, Apollo, Places)
├── prompts/    prompts système (garde-fous CDCF §0)
├── schemas/    contrats Pydantic (I/O)
└── api/v1/     endpoints (ares, builder, sourcing, costs, health)
tests/          pytest (provider mocké — pas d'appel réseau)
```

## Sources de prospection

`POST /api/v1/sourcing/executer` traduit un ICP en requêtes, interroge les sources
et rend des leads bruts, prêts pour `/ares/qualify`. Deux natures :

| Source | Nature | Clé | Ce qu'elle apporte |
|---|---|---|---|
| `hunter_discover` | découverte | `HUNTER_API_KEY` | entreprises par industrie, effectif, pays |
| `apollo` | découverte | `APOLLO_API_KEY` | entreprise + effectif + décideur + email |
| `google_maps` | découverte | `GOOGLE_PLACES_API_KEY` | TPE de quartier (nom + site web) |
| `hunter` | **enrichissement** | `HUNTER_API_KEY` | email nominatif du décideur, sur un domaine connu |
| `linkedin` | découverte | `APIFY_API_TOKEN` | décideurs LinkedIn (nom, poste, email, site de l'entreprise) — **voir la réserve ci-dessous** |
| `site_web` | — | — | non implémenté (nécessite l'extraction LLM) |

Apify n'est pas une source : c'est l'**exécutant** qui fait tourner l'acteur
`harvestapi/linkedin-profile-search`. La source, elle, reste `linkedin`.

L'exécution se fait en deux temps : **découverte** en parallèle → filtrage des
secteurs exclus → **enrichissement** des leads retenus. Filtrer avant d'enrichir
évite de dépenser des crédits sur des leads qu'on s'apprête à écarter.

`dry_run: true` (défaut) n'envoie aucune requête et renvoie les appels qui
*seraient* faits — clés masquées. C'est ce qui permet de valider la chaîne sans budget.

### ⚠️ LinkedIn — réserve juridique assumée

LinkedIn n'expose **aucune API de recherche** : l'acteur Apify scrape la plateforme,
ce qui contrevient à ses conditions d'utilisation. Passer par Apify déplace qui
exécute le scraping, pas la nature de l'acte. Les profils collectés sont en outre
des **données personnelles** (RGPD) : base légale et durée de conservation sont à
établir avant tout usage en production.

Le choix est délibéré et documenté ; l'avertissement remonte au client dans
`diagnostics` et dans `avertissement` du bloc de recherche, jamais enterré dans le
code. Apollo et Hunter atteignent les mêmes décideurs par voie contractuelle : les
préférer si l'usage doit rester strictement conforme.

**Coût.** 0,10 $ par page de recherche (25 profils) + 0,01 $ par profil avec
recherche d'email. Le connecteur calcule un plafond de dépense par exécution, à
partir de `limite`. Apify **refuse** un plafond inférieur à 0,10 $, et un plafond
trop juste fait réussir l'exécution en ne rendant **aucun** résultat — après avoir
facturé la recherche.

**Apify / LinkedIn, points de contrat vérifiés contre l'API** — aucun n'est dans la doc :
- `currentPosition` est **souvent vide** alors que la personne a un poste (2 profils
  sur 3 lors de la vérification) : le poste réel est dans `experience[0]` ;
- `emails` est une **liste d'objets** `{email, status, qualityScore}`, pas une chaîne ;
- le secteur **n'est pas attribué** : la recherche est floue, pas taxonomique — une
  requête « marketing » a ramené une académie de tennis. C'est `/ares/qualify` qui
  tranche ;
- l'effectif n'est pas rendu, alors même qu'on filtre dessus (comme Hunter Discover) ;
- le code de tranche `A` vaut « Self-Employed », **pas** « 1-10 ».

**Hunter, points de contrat vérifiés contre l'API** — ils ne sont pas dans la doc :
- les filtres s'enveloppent (`{"industry": {"include": [...]}}`) ; une liste nue est rejetée ;
- `industry` est une **nomenclature fermée** (`"Marketing Services"`, pas `"Marketing & Advertising"`) ;
- `limit`/`offset` sont réservés au plan Premium et font échouer tout l'appel ;
- `headquarters_location` exige un code pays ISO ; une ville seule est refusée ;
- une valeur de `headcount` hors catalogue est **silencieusement ignorée**, sans erreur ;
- Discover ne rend que `domain`, `organization` et `emails_count` — **ni secteur ni effectif** :
  le secteur d'un lead est celui qu'on a filtré, d'où une requête par secteur.

## Modèles Claude (CDCF §5.1)
- `fast` → **Claude Haiku 4.5** (`claude-haiku-4-5`) : classification.
- `reasoning` → **Claude Sonnet 4.6** (`claude-sonnet-4-6`) : qualification, génération.

## Garde-fous non-négociables (CDCF §0)
Jamais suggérer de « remplacer » un humain · sorties toujours structurées (JSON) ·
confiance faible → l'appelant traite manuellement · l'ICP a une source unique par
workspace, jamais dupliquée par agent · un agent ne peut pas exercer un poste qui
n'est pas le sien.
