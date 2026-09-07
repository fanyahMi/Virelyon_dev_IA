# Données de test — ARES &amp; Agent Builder

Jeux de données prêts à l'emploi pour tester le service IA **avec ce qui est disponible aujourd'hui**.

## Lancer les tests

1. Démarrer le service (dans un terminal) :
   ```bash
   make run            # → http://localhost:8080
   ```
2. Lancer le script (dans un autre terminal) :
   ```bash
   ./samples/run.sh
   # ou en surchargeant l'URL / la clé :
   AI_URL=http://localhost:8080 AI_KEY=demo-secret ./samples/run.sh
   ```
   > `AI_KEY` doit correspondre à `INTERNAL_API_KEY` de ton `.env`.

## Ce qui marche SANS clé Claude ✅ (logique pure / déterministe)

### Agent Builder (paramétrage du client)

| Fichier | Endpoint | Résultat attendu |
|---|---|---|
| — | `GET /api/v1/builder/referentiels` | vocabulaire des listes déroulantes (secteurs, rôles, tons, canaux) |
| `icp_valider_ok.json` | `POST /api/v1/builder/icp/valider` | `valide: true`, **aucun diagnostic**, `criteres_actifs: 3` |
| `icp_valider_contradiction.json` | `POST /api/v1/builder/icp/valider` | `valide: false` — 2 **erreurs** (min>max, secteur inclus ET exclu) |
| `icp_valider_hors_referentiel.json` | `POST /api/v1/builder/icp/valider` | `valide: true` + 4 avertissements (« Marketing digital » → `marketing`, « CEO » → `fondateur`, « plomberie » inconnu, fourchette 10-12 trop étroite) |
| `icp_valider_vide.json` | `POST /api/v1/builder/icp/valider` | `criteres_actifs: 0` — l'ICP ne filtre rien |
| `sourcing_linkedin.json` | `POST /api/v1/sourcing/executer` | plan LinkedIn/Apify **simulé** : 1 requête par secteur, jeton masqué, plafond de dépense calculé, + 2 avertissements (CGU/RGPD, recherche floue) |

### Scoring ARES

| Fichier | Endpoint | Résultat attendu |
|---|---|---|
| `score_bon.json` | `POST /api/v1/ares/score` | ~**96** → palier `quasi_parfait` (ICP, complet, 2 signaux) |
| `score_tres_forte.json` | `POST /api/v1/ares/score` | ~**93** → palier `tres_forte` (ICP, complet, 1 signal) |
| `score_correcte.json` | `POST /api/v1/ares/score` | ~**72** → palier `correcte` (rôle hors cibles → fit partiel) |
| `score_faible.json` | `POST /api/v1/ares/score` | ~**28** → palier `faible` (ancien + incomplet mais dans l'ICP) |
| `score_poids_custom.json` | `POST /api/v1/ares/score` | pondérations personnalisées (`scoring_config`, engagement fort) |
| `score_hors_icp.json` | `POST /api/v1/ares/score` | **score bas** (secteur exclu → `fit = 0`) |
| `score_incomplet.json` | `POST /api/v1/ares/score` | score bas (faible complétude) |
| `decide_plafond.json` | `POST /api/v1/ares/decide` | **`"action": "arrêt"`** (plafond 3/3 atteint, `meta: null`) |
| — | `GET /api/v1/costs/{id}` | coûts cumulés du workspace + détail `par_agent` |
| — | `GET /health` | `{"status":"ok"}` |

> Les 4 paliers du CDCF §4.3.1 sont couverts : `quasi_parfait` / `tres_forte` / `correcte` / `faible`.

### L'équipe d'agents du client

Le workspace `1111…1111` a deux agents actifs : un **prospecteur** et un **relanceur**.

| Fichier | Endpoint | Résultat attendu |
|---|---|---|
| — | `GET /api/v1/builder/config/{id}` | l'ICP du client + ses 2 agents (missions, voix et canaux distincts) |
| `agent_score_prospecteur.json` | `POST /api/v1/ares/score` | **81** — pondérations du prospecteur (fit 0.40, engagement 0.10) |
| `agent_score_relanceur.json` | `POST /api/v1/ares/score` | **78** — *même lead*, pondérations du relanceur (engagement 0.40) |
| `agent_refus_prospecteur_classify.json` | `POST /api/v1/ares/classify` | **403** — lire une réponse est le poste du relanceur |
| `agent_refus_relanceur_sourcing.json` | `POST /api/v1/sourcing/executer` | **403** — prospecter est le poste du prospecteur |
| `agent_inactif.json` | `POST /api/v1/ares/generate` | **409** — cet agent n'a pas été activé par le client |

> Les deux fichiers `agent_score_*` ne diffèrent que par leur `agent_id` : c'est la
> démonstration la plus courte que la configuration de l'agent pilote bien le résultat.

## Ce qui NÉCESSITE `ANTHROPIC_API_KEY` ⚠️ (sinon 503 propre)

| Fichier | Endpoint | Modèle | Résultat attendu |
|---|---|---|---|
| `icp_extraire.json` | `POST /api/v1/builder/icp/extraire` | Claude Sonnet 4.6 | ICP structuré : `["communication","marketing"]`, 5-30, `["fondateur"]`, exclut `hotellerie` |
| `icp_extraire_vague.json` | `POST /api/v1/builder/icp/extraire` | Claude Sonnet 4.6 | **confiance basse**, ICP quasi vide — aucune fourchette inventée |
| `qualify.json` | `POST /api/v1/ares/qualify` | Claude Sonnet 4.6 | `qualifie: true` |
| `qualify_hors_icp.json` | `POST /api/v1/ares/qualify` | Claude Sonnet 4.6 | `qualifie: false` (secteur exclu, hors taille, stagiaire) |
| `classify.json` | `POST /api/v1/ares/classify` | Claude Haiku 4.5 | `Intéressé` |
| `classify_plus_tard.json` | `POST /api/v1/ares/classify` | Claude Haiku 4.5 | `À recontacter plus tard` (+ `date_relance`) |
| `classify_pas_interesse.json` | `POST /api/v1/ares/classify` | Claude Haiku 4.5 | `Pas intéressé` |
| `classify_retrait.json` | `POST /api/v1/ares/classify` | Claude Haiku 4.5 | `Demande de retrait` |
| `classify_hors_scope.json` | `POST /api/v1/ares/classify` | Claude Haiku 4.5 | `Question hors-scope` |
| `generate.json` | `POST /api/v1/ares/generate` | Claude Sonnet 4.6 | message de 1er contact (J0) |
| `generate_relance.json` | `POST /api/v1/ares/generate` | Claude Sonnet 4.6 | relance J+7 tenant compte de l'historique |
| `agent_generate_prospecteur.json` | `POST /api/v1/ares/generate` | Claude Sonnet 4.6 | 1er contact — mission du prospecteur, `canal: email` (son seul canal) |
| `agent_generate_relanceur.json` | `POST /api/v1/ares/generate` | Claude Sonnet 4.6 | relance — mission du relanceur, ton chaleureux, email ou linkedin |
| `decide_continuer.json` | `POST /api/v1/ares/decide` | Claude (plafond non atteint) | `continuer` |
| `decide_escalade.json` | `POST /api/v1/ares/decide` | Claude (plafond non atteint) | `escalade` (réponse positive) |

> Les 5 catégories de réponse du CDCF sont couvertes par les fichiers `classify_*`.

Pour les activer : mettre une vraie clé dans `.env` → `ANTHROPIC_API_KEY=sk-ant-...`

## Curl manuel (exemple)
```bash
curl -X POST http://localhost:8080/api/v1/ares/score \
  -H "X-Internal-Key: demo-secret" -H "Content-Type: application/json" \
  --data @samples/score_bon.json
```

## Les leads fournis (pour comprendre le scoring)
- **Studio Créa** (`score_bon`) : marketing, 18 salariés, fondateur, complet, 2 signaux → cas idéal (`quasi_parfait`).
- **Cabinet Lumen** (`score_tres_forte` / `decide_escalade`) : conseil, décideur, complet, 1 signal → `tres_forte`.
- **Atelier Vertigo** (`score_correcte`) : rôle `manager` **hors cibles** → fit partiel → `correcte`.
- **Prospect Dormant** (`score_faible`) : dans l'ICP mais **ancien** (mars) et incomplet → `faible`.
- **Signal Fort SARL** (`score_poids_custom`) : 3 signaux, testé avec un `scoring_config` orienté engagement.
- **Hôtel du Port** (`score_hors_icp`) : secteur `hotellerie` **exclu** → éliminé par le fit.
- **Entreprise Inconnue** (`score_incomplet`) : quasi vide → faible complétude.
- **Grande Distribution SA** (`qualify_hors_icp`) : secteur exclu, 450 salariés, stagiaire → `qualifie: false`.
- **Agence Nova** (`qualify` / `generate` / `generate_relance`) : communication, décideur, signal de recrutement.

> Les dates de `ingested_at` sont calées pour la fraîcheur au **2026-07-22** (recalibrer si tu testes bien plus tard).
