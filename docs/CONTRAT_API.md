# Contrat d'API — Service IA (pour le backend)

> **La seule chose dont le backend a besoin pour appeler le service IA.**
> Le service est **stateless** : le backend passe les données dans la requête, l'IA
> renvoie une décision en JSON. Le backend **persiste** et gère l'isolation (RLS).
> Le Swagger vivant : `http://<host>/docs`.

---

## 1. Généralités

| | |
|---|---|
| **Base URL (dev)** | `http://localhost:8080` |
| **Auth** | header **`X-Internal-Key: <secret>`** sur toutes les routes `/api/v1/...` |
| **Content-Type** | `application/json` |
| **Multi-tenant** | `workspace_id` (UUID) **obligatoire** dans chaque requête métier |
| **Multi-agent** | `agent_id` (UUID) **facultatif** — voir §1 bis |
| **CORS** | désactivé (appels **server-à-server** uniquement) |

> `/health` est public (pas de header). Tout le reste exige `X-Internal-Key`.

---

## 1 bis. Les agents d'un client

Un workspace n'a pas *un* ARES mais une **équipe d'agents nommés**, chacun tenant un
poste. Deux rôles sont ouverts aujourd'hui :

| Rôle | Métier | Capacités |
|---|---|---|
| `prospecteur` | trouver, trier, engager le premier contact | `sourcing`, `qualify`, `score`, `generate` |
| `relanceur` | entretenir la conversation ouverte jusqu'à l'escalade | `score`, `generate`, `classify`, `decide` |

**`agent_id` est facultatif.** Sans lui, l'appel se comporte exactement comme avant :
le backend fournit tout dans le corps et décide seul. Rien à migrer dans l'immédiat.

**Avec lui**, le service vérifie — dans cet ordre — que l'agent appartient à ce
workspace (404 sinon), qu'il est `actif` (409 sinon) et que son rôle autorise l'acte
demandé (403 sinon). Puis **sa configuration l'emporte** sur ce que joint l'appelant :
mission (`objectif_principal`), voix (`ton_de_voix`), canaux (`canaux_actifs`),
pondérations (`scoring_config`) et paliers de relance. C'est délibéré — laisser une
requête redéfinir le ton au coup par coup rouvrirait la dérive que l'Agent Builder
existe pour empêcher.

> L'**ICP reste au niveau du workspace** (source unique, CDCF §0 garde-fou n°2) :
> il est partagé par tous les agents du client. Seule la manière de travailler
> appartient à l'agent.

---

## 2. Codes de réponse

| Code | Signification | Action côté backend |
|---|---|---|
| `200` | OK | lire la réponse |
| `401` | `X-Internal-Key` manquante/invalide | vérifier le secret partagé |
| `403` | **l'agent n'a pas cette capacité** | appeler avec l'agent dont c'est le poste |
| `404` | **agent inconnu dans ce workspace** | vérifier `agent_id` (ou l'omettre) |
| `409` | **agent non activé par le client** | l'activer dans l'Agent Builder |
| `422` | payload invalide (validation) | corriger le corps de la requête |
| `429` | **plafond de coût du workspace atteint** | ne plus appeler ce workspace (ou relever le plafond) |
| `502` | réponse LLM invalide | réessayer / journaliser |
| `503` | LLM non configuré ou indisponible | vérifier `ANTHROPIC_API_KEY` / réessayer |

---

## 3. Objets partagés (le backend les envoie)

**Lead**
```json
{
  "nom": "Studio Créa",
  "secteur": "marketing",
  "taille_effectif": 18,
  "role_contact": "fondateur",
  "contact": { "email": "contact@studiocrea.co" },
  "montant_potentiel": 8000,
  "donnees_brutes": { "signaux_bruts": [ { "type": "levee_de_fonds", "detail": "Série A 3M€" } ] },
  "ingested_at": "2026-07-20T09:00:00Z",
  "langue": "fr"
}
```
*(seul `nom` est requis ; les autres champs améliorent la décision.)*

**ICP** (profil client idéal — le backend le lit depuis `workspace_icp_config`)
```json
{
  "secteurs_inclus": ["marketing", "conseil"],
  "secteurs_exclus": ["hotellerie"],
  "taille_min": 5,
  "taille_max": 30,
  "roles_cibles": ["fondateur", "decideur"]
}
```

**Meta** (jointe à chaque réponse issue d'un appel LLM)
```json
{ "model_used": "claude-sonnet-4-6", "usage": { "input_tokens": 512, "output_tokens": 80 }, "cost_estimate": 0.0021, "cached": false }
```

---

## 4. Endpoints

### `POST /api/v1/ares/qualify` — Qualification (Claude Sonnet)
**Entrée**
```json
{ "workspace_id": "UUID", "lead": { … }, "icp": { … } }
```
**Sortie**
```json
{ "qualifie": true, "confiance": 0.9, "motif": "correspond à l'ICP", "meta": { … } }
```

### `POST /api/v1/ares/score` — Scoring (logique pure, sans LLM)
**Entrée** — `scoring_config` optionnel (pondérations par workspace)
```json
{ "workspace_id": "UUID", "lead": { … }, "icp": { … },
  "scoring_config": { "poids_fraicheur": 0.25, "poids_completude": 0.25, "poids_fit": 0.4, "poids_engagement": 0.1 } }
```
**Sortie**
```json
{ "score": 96, "breakdown": { "fraicheur": 1.0, "completude": 1.0, "fit": 1.0, "engagement": 0.6, "poids": { … } },
  "palier": { "nom": "quasi_parfait", "relances_max": 5, "cadence": [0,3,7,12,18,25] } }
```

### `POST /api/v1/ares/generate` — Génération de message (Claude Sonnet)
**Entrée**
```json
{ "workspace_id": "UUID", "lead": { … }, "etape": "J0",
  "ton_de_voix": "professionnel", "historique": [], "language": "fr" }
```
**Sortie**
```json
{ "texte": "Bonjour, …", "canal": "email", "meta": { … } }
```

### `POST /api/v1/ares/classify` — Classification de réponse (Claude Haiku)
**Entrée**
```json
{ "workspace_id": "UUID", "message_entrant": "Oui, ça m'intéresse…", "language": "fr" }
```
**Sortie** — `categorie` ∈ { Intéressé, À recontacter plus tard, Pas intéressé, Demande de retrait, Question hors-scope }
```json
{ "categorie": "Intéressé", "confiance": 0.8, "date_relance": null, "meta": { … } }
```

### `POST /api/v1/ares/decide` — Décision de prochaine action
**Entrée** — inclut le palier et le compteur de relances (v1.1)
```json
{ "workspace_id": "UUID", "lead": { … },
  "palier": { "nom": "correcte", "relances_max": 3, "cadence": [0,4,10,18] },
  "relances_effectuees": 1, "contexte": "Aucune réponse après J0 et J+4." }
```
**Sortie** — `action` ∈ { continuer, pause, escalade, arrêt }. `meta: null` si décision **déterministe** (plafond atteint, aucun appel LLM).
```json
{ "action": "continuer", "justification": "score correct, marge de relance", "meta": { … } }
```

---

## 5. Agent Builder — paramétrage du client

Ces endpoints alimentent l'écran **Agent Builder**. Ils ne persistent rien : le backend
enregistre le résultat dans `workspace_icp_config`.

### `GET /api/v1/builder/referentiels` — Vocabulaire du filtrage (aucun LLM)

**La source unique des listes déroulantes du front.** Le filtrage ICP compare des valeurs
avec une **égalité stricte** : `"Marketing digital"` ne matche PAS `"marketing"`. Laisser
saisir du texte libre garantit un fit à 0 sur des leads valides.

```json
{ "secteurs": ["marketing", "communication", "conseil", …],
  "secteurs_services_b2b": ["marketing", …],
  "secteurs_hors_icp": ["hotellerie", "restauration", …],
  "roles": ["fondateur", "decideur", "directeur", "manager", "operationnel"],
  "tons_de_voix": ["professionnel", "chaleureux", "direct", "creatif"],
  "canaux": ["email", "whatsapp", "linkedin", "sms", "slack"] }
```

À appeler une fois au chargement de l'écran.

### `POST /api/v1/builder/icp/valider` — Vérification d'un ICP (aucun LLM)

Gratuit et instantané — appelable à chaque modification du formulaire.

**Entrée**
```json
{ "icp": { "secteurs_inclus": ["marketing"], "secteurs_exclus": ["marketing"],
           "taille_min": 30, "taille_max": 5, "roles_cibles": ["fondateur"] } }
```
**Sortie** — `valide: false` s'il existe au moins un diagnostic de niveau `erreur`
```json
{ "valide": false,
  "diagnostics": [
    { "niveau": "erreur", "champ": "taille_effectif",
      "message": "La taille minimale (30) dépasse la taille maximale (5) : aucun lead ne peut correspondre.",
      "suggestion": "Inverser les deux valeurs." }
  ],
  "criteres_actifs": 3 }
```

| Contrôle | Niveau |
|---|---|
| `taille_min` > `taille_max` | **erreur** |
| Secteur à la fois inclus et exclu | **erreur** |
| Valeur hors référentiel (« plomberie ») | avertissement |
| Valeur non normalisée (« Marketing digital » → « marketing ») | avertissement |
| Aucun critère renseigné (le filtrage ne discrimine rien) | avertissement |
| Fourchette d'effectif très étroite | avertissement |
| ICP très sélectif (1 secteur + 1 rôle + fourchette) | avertissement |
| Secteur hors services B2B en inclusion | avertissement |

> `criteres_actifs` (0 à 3) compte les critères réellement discriminants : secteur, taille, rôle.
> **0 signifie que tous les leads obtiendront le même fit neutre.**

### `POST /api/v1/builder/icp/extraire` — Texte libre → ICP structuré (Claude Sonnet)

Le client décrit sa cible en langage normal ; on en tire un ICP exploitable.

**Entrée**
```json
{ "workspace_id": "UUID",
  "texte": "Agences de communication et de marketing digital, 5 à 30 salariés, je vise les fondateurs. Pas d'hôtellerie.",
  "language": "fr" }
```
**Sortie**
```json
{ "icp": { "secteurs_inclus": ["communication", "marketing"], "secteurs_exclus": ["hotellerie"],
           "taille_min": 5, "taille_max": 30, "roles_cibles": ["fondateur"] },
  "confiance": 0.9,
  "non_reconnu": [],
  "diagnostics": [],
  "meta": { … } }
```

Garanties :
- Le résultat est **renormalisé sur le référentiel** après l'appel — une valeur inventée par
  le modèle n'entre jamais dans l'ICP, elle ressort dans `non_reconnu`.
- **Aucune fourchette d'effectif inventée** : si le client n'en parle pas, `taille_min` et
  `taille_max` restent `null` (jamais de « 5-30 » par défaut).
- Aucune zone géographique déduite.
- Texte vague → `confiance` basse. À afficher au client plutôt que d'appliquer en silence.

> Le résultat est une **proposition** : il doit rester modifiable dans le formulaire avant
> enregistrement, jamais appliqué automatiquement.

---

### `GET /api/v1/builder/config/{workspace_id}` — Le client et son équipe (aucun LLM)

L'ICP et la zone sont au niveau du workspace ; la mission, la voix et les canaux
appartiennent à chaque agent. Un client sans configuration reçoit **200** avec une
équipe par défaut (un prospecteur, un relanceur, tous deux `configuration_incomplete`,
en supervision, email seul) — un compte neuf n'est pas une erreur.

```json
{ "workspace_id": "UUID", "zone": "Genève", "langue": "fr",
  "icp": { "secteurs_inclus": ["conseil"], "taille_min": 5, "taille_max": 30, … },
  "agents": [
    { "agent_id": "UUID", "role": "prospecteur", "nom": "Prospecteur",
      "objectif_principal": "…", "ton_de_voix": "professionnel", "statut": "actif",
      "autonomy_level": "supervision", "canaux_actifs": ["email"],
      "scoring_config": {…}, "paliers": […], "seuil_confiance": 0.7 },
    { "agent_id": "UUID", "role": "relanceur", … }
  ] }
```

> Les `agent_id` sont **stables** : ils sont dérivés du workspace et du rôle, donc
> identiques d'un appel à l'autre même si rien n'est encore persisté.

### `GET /api/v1/builder/config/{workspace_id}/agents/{agent_id}` — Un agent précis

Rend le seul objet `ConfigAgent`, ou **404** si l'agent n'appartient pas à ce workspace.

---

### `GET /api/v1/costs/{workspace_id}` — Coûts cumulés (pour FINANCE)

Le total engage (c'est lui que le plafond protège) ; `par_agent` sert au diagnostic —
savoir *lequel* des agents a dépensé. La clé `"-"` regroupe les appels sans `agent_id`.

```json
{ "workspace_id": "UUID", "input_tokens": 1520, "output_tokens": 340, "cost": 0.0057,
  "par_agent": {
    "dec21ef5-…": { "input_tokens": 1200, "output_tokens": 260, "cost": 0.0043 },
    "-":          { "input_tokens": 320,  "output_tokens": 80,  "cost": 0.0014 }
  } }
```

### `GET /health` — Santé (public)
```json
{ "status": "ok", "service": "virelyon-ai" }
```

---

## 5. Exemple d'appel
```bash
curl -X POST http://localhost:8080/api/v1/ares/score \
  -H "X-Internal-Key: $INTERNAL_API_KEY" -H "Content-Type: application/json" \
  --data '{"workspace_id":"11111111-1111-1111-1111-111111111111",
           "lead":{"nom":"Studio Créa","secteur":"marketing","taille_effectif":18,"role_contact":"fondateur"},
           "icp":{"secteurs_inclus":["marketing"],"taille_min":5,"taille_max":30,"roles_cibles":["fondateur"]}}'
```

---

## 6. Ce que le backend doit faire de son côté
- **Persister** les résultats (leads, statuts, `messages`, `lead_events`) — l'IA ne touche pas la base.
- **Appliquer l'isolation multi-tenant** (RLS) — l'IA ne fait que recevoir `workspace_id`.
- **Fournir l'ICP** (depuis `workspace_icp_config`) dans les requêtes qualify/score.
- **Gérer les secrets de canal** (Orange, CRM) — hors périmètre IA.
- **Orchestrer** (séquençage, relances, envois) via n8n ; appeler l'IA aux points de décision.
- **Joindre `agent_id`** quand l'action est portée par un agent nommé — c'est ce qui applique
  sa configuration et interdit qu'il déborde de son poste. Facultatif, mais recommandé.

## 7. Ce que le backend n'a PAS à faire
- Connaître la structure interne du service IA (seul le contrat ci-dessus compte).
- Savoir si un endpoint utilise Claude ou une logique pure (transparent).
- Gérer les modèles/prompts (côté DEV IA).

> **Point de coordination unique :** ce contrat + le header d'auth + le déploiement.
> La structure interne du service IA reste à l'équipe DEV IA (voir `../STRUCTURE_ET_AUTONOMIE.md`).
