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
| **CORS** | désactivé (appels **server-à-server** uniquement) |

> `/health` est public (pas de header). Tout le reste exige `X-Internal-Key`.

---

## 2. Codes de réponse

| Code | Signification | Action côté backend |
|---|---|---|
| `200` | OK | lire la réponse |
| `401` | `X-Internal-Key` manquante/invalide | vérifier le secret partagé |
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

**Meta** (jointe à chaque réponse issue d'un appel LLM — `null` quand la décision est déterministe, sans appel LLM)
```json
{ "model_used": "claude-sonnet-4-6", "usage": { "input_tokens": 512, "output_tokens": 80 }, "cost_estimate": 0.0021, "cached": false }
```

---

## 4. Endpoints — Agent ARES (prospection)

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

## 4bis. Endpoints — Agent APEX (support client, CDCF APEX v2.0)

Même principe stateless : les fragments de la base de connaissances sont
retrouvés en amont par pgvector (Big Data/n8n) et fournis dans la requête —
APEX ne touche jamais pgvector ni la base de données. Détail complet des
garde-fous et du flux : `docs/APEX.md`.

### `POST /api/v1/apex/classify` — Compréhension & recherche contextuelle (Claude Haiku)
**Entrée**
```json
{ "workspace_id": "UUID", "message_entrant": "Comment obtenir un remboursement ?",
  "historique": [], "seuil_pertinence": 0.5,
  "fragments_candidats": [ { "chunk_texte": "Les remboursements se font sous 14 jours.", "score": 0.7 } ] }
```
**Sortie** — `intention` ∈ { question_produit, reclamation, hors_scope, demande_humain }.
`necessite_escalade` est calculé **en Python** à partir du seuil configuré, jamais laissé au modèle.
```json
{ "intention": "question_produit",
  "fragments_pertinents": [ { "chunk_texte": "Les remboursements se font sous 14 jours.", "score": 0.92 } ],
  "confiance": 0.85, "langue_detectee": "fr", "necessite_escalade": false, "meta": { … } }
```

### `POST /api/v1/apex/generate` — Génération de réponse, avec sélection d'outil intégrée (Claude Sonnet)
**Entrée** — `seuil_pertinence` doit reprendre `agent_config.seuil_pertinence` du workspace
```json
{ "workspace_id": "UUID",
  "fragments_pertinents": [ { "chunk_texte": "Les remboursements se font sous 14 jours.", "score": 0.92 } ],
  "historique": [], "ton_de_voix": "formel", "langue": "fr",
  "outils_actifs": ["get_customer_context"], "resultat_outil": null, "seuil_pertinence": 0.5 }
```
**Sortie** — si `appel_outil_demande` est rempli, `texte` est vide : le backend/n8n doit
exécuter l'outil demandé puis rappeler `/apex/generate` avec `resultat_outil` rempli.
```json
{ "texte": "Le remboursement est traité sous 14 jours ouvrés. Souhaitez-vous que je vous mette en contact avec un conseiller ?",
  "confiance": 0.9, "justification": "fondé sur le fragment fourni", "necessite_escalade": false,
  "appel_outil_demande": null, "meta": { … } }
```
**Règle absolue, vérifiée EN PYTHON (pas seulement dans le prompt)** : `fragments_pertinents`
vide **OU** tous les scores sous `seuil_pertinence` ⇒ **aucun appel LLM**, `meta: null`,
`necessite_escalade: true` — APEX ne répond jamais depuis sa connaissance générale :
```json
{ "texte": "", "confiance": 0.0, "necessite_escalade": true,
  "justification": "Aucun contenu suffisamment pertinent dans la base de connaissances (seuil de pertinence non atteint) — garde-fou anti-hallucination …",
  "appel_outil_demande": null, "meta": null }
```

### `POST /api/v1/apex/escalade` — Détection de confiance & décision d'escalade (Claude Sonnet)
**Entrée**
```json
{ "workspace_id": "UUID", "message_entrant": "Toujours pas de réponse, c'est inadmissible !",
  "historique": [], "intention": "reclamation", "regles_escalade": [],
  "nb_echanges_sans_resolution": 2, "tentatives_contournement_precedentes": 0 }
```
**Sortie** — `decision` ∈ { continuer, brouillon, escalade }. `meta: null` si la décision est
**déterministe** (demande humaine explicite, plafond d'échanges atteint, récidive de contournement).
```json
{ "sentiment": "negatif", "declencheurs_actifs": ["sentiment_negatif"], "tentative_contournement": false,
  "decision": "escalade", "justification": "sentiment négatif marqué", "meta": { … } }
```

### `POST /api/v1/apex/decide-action` — Décision finale (logique pure, sans LLM)
**Entrée**
```json
{ "niveau_autonomie": "semi_autonome", "intention": "question_produit", "confiance": 0.9,
  "decision_escalade": "continuer", "seuil_confiance_semi_autonome": 0.8, "cloture_demandee": false }
```
**Sortie** — `action` ∈ { repondre, brouillon, escalade, cloturer }
```json
{ "action": "repondre", "justification": "Niveau Semi-autonome : intention question_produit à confiance élevée — envoi automatique." }
```

### `GET /api/v1/apex/config/{workspace_id}` — Configuration du client (aucun LLM)
```json
{ "workspace_id": "UUID", "statut": "actif", "ton_de_voix": "amical", "langue": "fr",
  "canaux_actifs": ["chat_web", "email"], "quotas_par_canal": { "chat_web": 200 },
  "niveau_autonomie": "semi_autonome", "outils_actifs": ["get_customer_context"],
  "seuil_pertinence": 0.55, "seuil_confiance_semi_autonome": 0.8, "regles_escalade": [] }
```
> Un client sans configuration reçoit le défaut prudent : `statut: configuration_incomplete`, `niveau_autonomie: supervise`, `canaux_actifs: ["chat_web"]` — jamais d'envoi automatique tant que le client n'a rien réglé.

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

### `GET /api/v1/costs/{workspace_id}` — Coûts cumulés (pour FINANCE)
```json
{ "workspace_id": "UUID", "input_tokens": 1520, "output_tokens": 340, "cost": 0.0057 }
```

### `GET /health` — Santé (public)
```json
{ "status": "ok", "service": "virelyon-ai" }
```

---

## 5bis. Exemple d'appel
```bash
curl -X POST http://localhost:8080/api/v1/ares/score \
  -H "X-Internal-Key: $INTERNAL_API_KEY" -H "Content-Type: application/json" \
  --data '{"workspace_id":"11111111-1111-1111-1111-111111111111",
           "lead":{"nom":"Studio Créa","secteur":"marketing","taille_effectif":18,"role_contact":"fondateur"},
           "icp":{"secteurs_inclus":["marketing"],"taille_min":5,"taille_max":30,"roles_cibles":["fondateur"]}}'
```

---

## 6. Ce que le backend doit faire de son côté
- **Persister** les résultats (leads, statuts, `messages`, `lead_events`, `conversations`, `messages_apex`, `conversation_events`) — l'IA ne touche pas la base.
- **Appliquer l'isolation multi-tenant** (RLS) — l'IA ne fait que recevoir `workspace_id`.
- **Fournir l'ICP** (depuis `workspace_icp_config`) dans les requêtes qualify/score.
- **Fournir les fragments candidats APEX** (recherche vectorielle pgvector faite en amont, par n8n/Big Data) dans les requêtes `/apex/classify`.
- **Exécuter les outils APEX** (ex. `get_customer_context` contre le CRM du client) quand `/apex/generate` renvoie `appel_outil_demande` — l'IA ne fait qu'décider, jamais qu'exécuter (§2.4 du CDCF APEX).
- **Gérer les secrets de canal** (Orange, CRM) — hors périmètre IA.
- **Orchestrer** (séquençage, relances, envois, réception multicanal APEX) via n8n ; appeler l'IA aux points de décision.

## 7. Ce que le backend n'a PAS à faire
- Connaître la structure interne du service IA (seul le contrat ci-dessus compte).
- Savoir si un endpoint utilise Claude ou une logique pure (transparent).
- Gérer les modèles/prompts (côté DEV IA).

> **Point de coordination unique :** ce contrat + le header d'auth + le déploiement.
> La structure interne du service IA reste à l'équipe DEV IA (voir `../STRUCTURE_ET_AUTONOMIE.md`).
