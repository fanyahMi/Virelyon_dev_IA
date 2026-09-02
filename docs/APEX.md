# APEX — Documentation détaillée (Agent de support client)

> Documentation complète de l'agent **APEX** de VIRELYON : rôle, architecture,
> fonctionnement des 14 modules, modèle de données, prompts, garde-fous, flow,
> et comment le **service IA** de ce dépôt l'implémente.
> Source : CDCF APEX v2.0 (« Édition Enrichie »). Voir aussi `../DOCUMENTATION.md`
> (le service), `docs/ARES.md` (l'agent jumeau) et `../samples/README.md`.

---

## 1. Qu'est-ce qu'APEX ?

**APEX** est l'agent **agentique de support** de VIRELYON (SaaS d'agents IA pour
agences de services B2B).

**Sa promesse :** répondre aux questions des clients finaux du client VIRELYON, à
partir de la base de connaissances que ce client a lui-même fournie, sur le canal
de son choix ; agir quand une action concrète est nécessaire (consulter un
historique, ouvrir un ticket) ; et transmettre à un humain dès que la situation le
requiert — **sans jamais laisser un client final sans réponse ni sans recours humain**.

### Ce qui le rend « agentique » (v2.0, ≠ chatbot RAG)
Deux capacités, pas une seule :
1. **Prise de décision contextuelle** (répondre / escalader / continuer) — déjà présente en v1.0.
2. **Capacité d'agir** — interroger un CRM, ouvrir un ticket, journaliser une décision — via des
   outils explicitement définis (function calling), plutôt que de se limiter à produire du texte.
   C'est l'ajout central de la v2.0 : sans lui, APEX reste « un chatbot RAG avancé », pas un agent
   au sens Decagon / Sierra AI / Intercom Fin.

### Ce qu'APEX n'est PAS
- ❌ Un remplaçant du conseiller support (positionnement validé : **« augmenter », jamais « remplacer »** — critère de recette, pas une préférence de style).
- ❌ Un générateur de réponses génériques non fondées (garde-fou anti-hallucination : toute réponse est fondée exclusivement sur les documents fournis par le client).
- ❌ Un outil qui agit sans traçabilité (chaque décision et chaque appel d'outil est journalisé).

---

## 2. Position dans l'écosystème VIRELYON

```
Big Data (indexe la base de connaissances, pgvector partagé avec ARES)
    │
    ▼
APEX (comprend, consulte si besoin, répond ou escalade)
    │
    ▼
équipe support du client (reprend la main si nécessaire)
    │
    ▼
AURA (agrège les événements → rapports de performance support / satisfaction)
```

APEX **ne calcule jamais lui-même d'agrégat de reporting** : il produit des
événements structurés, consommés par AURA — même principe que côté ARES.

Différence structurelle avec ARES : ARES sort chercher des prospects (**push**,
sortant) ; APEX répond à des demandes entrantes (**pull**, réactif).

---

## 3. Architecture & principe de fonctionnement

### Stack (identique à ARES — zéro dette technique nouvelle)
- **Orchestration :** n8n — chaque module = un ou plusieurs workflows indépendants.
- **Raisonnement :** Claude API, appelé à chaque point de décision.
- **Mémoire :** Supabase Postgres + pgvector — mêmes tables que la mémoire d'ARES, tables dédiées à APEX.
- **Canaux :** widget de chat web (Typebot), email, WhatsApp, Slack — chacun un connecteur n8n indépendant, tous optionnels (APEX fonctionne à 100 % avec un seul canal actif).
- **Actions :** un jeu d'outils explicites (function calling) exposés à Claude — voir §2.4 du CDCF.

### Schéma des couches (§2.3)
```
Base de connaissances (upload client)
  → Indexation vectorielle (pgvector, Big Data)
  → Réception multicanal (§4.2)
  → Compréhension & recherche contextuelle — RAG (§4.3)
  → Sélection d'outil si nécessaire (§4.11)
  → Génération de réponse (§4.4)
  ⇄ Détection de confiance & niveau d'autonomie (§4.5, §4.14)
  → réponse envoyée | brouillon soumis à validation | escalade / handoff humain (§4.6)
  → Observabilité (événements, §4.10)
  → AURA (reporting agrégé)
```

### Ce que ce service IA fait, et ce qu'il ne fait PAS
Même frontière que pour ARES (service **stateless**) :
- **Ne touche jamais pgvector ni la base de données.** Les fragments candidats de
  la base de connaissances arrivent déjà retrouvés (recherche vectorielle faite
  par Big Data / n8n), exactement comme `lead.donnees_brutes` côté ARES.
- **N'exécute jamais un outil.** Il *décide* (get_customer_context à appeler, ou
  non) ; c'est n8n qui exécute l'appel HTTP réel vers le CRM/Helpdesk du client
  (§2.4 : « les outils sont des appels HTTP simples orchestrés par n8n »).
- **N'écrit jamais d'événement lui-même** (`conversation_events`) — il renvoie les
  données structurées (justification, confiance, décision) que le backend/n8n
  transforme en événement.

---

## 4. Les modules fonctionnels — ce qui est implémenté ici

| Module CDCF | Portée du service IA (ce dépôt) | Statut |
|---|---|---|
| 4.1 Ingestion & indexation | Big Data / n8n (hors service IA) | — |
| 4.2 Réception multicanal | n8n (hors service IA) | — |
| **4.3 Compréhension & RAG** | `POST /apex/classify` | ✅ implémenté |
| **4.4 Génération de réponse (+ 4.11 sélection d'outil)** | `POST /apex/generate` | ✅ implémenté |
| **4.5 Détection de confiance & escalade** | `POST /apex/escalade` | ✅ implémenté |
| 4.6 Escalade / Handoff (notification, ticket) | n8n + backend (hors service IA) | — |
| 4.7 Mémoire & continuité | pgvector, Big Data (hors service IA) | — |
| 4.8 Multilinguisme | `langue_detectee` (sortie de `/apex/classify`) | ✅ implémenté |
| 4.9 Satisfaction & feedback | backend (persistance, hors service IA) | — |
| 4.10 Observabilité (taxonomie d'événements) | backend/n8n écrit l'événement à partir des réponses de ce service | — |
| 4.11 Actions & function calling | intégré à `POST /apex/generate` (voir §6) | ✅ implémenté (MVP) |
| 4.12 Jeu d'évaluation avant activation | backend (tableau de contrôle, hors service IA) | — |
| 4.13 Sécurité conversationnelle & anti-abus | anti-injection dans les prompts + détection de contournement dans `/apex/escalade` | ✅ implémenté |
| **4.14 Niveaux de confiance opérationnelle** | `POST /apex/decide-action` | ✅ implémenté (logique pure) |

Ce service couvre la partie **DEV IA** d'APEX (décisions + logique), en
**stateless**. Ingestion, canaux, persistance, RLS, notification, ticketing
restent côté Big Data / n8n / backend — même répartition que pour ARES.

---

## 5. Modèle de données (tables APEX, CDCF §3)

Aucune de ces tables n'est possédée par ce service (il n'a pas d'accès base de
données) — elles sont listées ici pour comprendre le contrat des endpoints.

| Table | Rôle |
|---|---|
| `knowledge_base_documents` | documents/FAQ uploadés par le client |
| `apex_knowledge_chunks` | mémoire vectorielle (pgvector) — jamais interrogée directement par ce service |
| `conversations` | pipeline actif — `statut` (dont **Brouillon en attente**, nouveau v2.0), `niveau_autonomie_applique` |
| `messages_apex` | historique — `statut_validation` (nouveau v2.0) |
| `regles_escalade` | déclencheurs d'escalade configurables → correspond à `RegleEscalade` (`app/schemas/apex.py`) |
| `agent_config` (apex) | réglages d'exécution → correspond à `ConfigAgentApex` |
| `feedback_satisfaction` | collecte optionnelle en fin de conversation |
| `conversation_events` | journal d'observabilité, consommé par AURA |
| `outil_appels` (nouveau v2.0) | journal des appels d'outils |
| `eval_jeu_de_test` (nouveau v2.0) | jeu de test avant activation |

---

## 6. Les points d'appel à Claude (prompts §5) — et un choix de conception à valider

| Prompt CDCF | Endpoint du service | Entrée | Sortie (JSON structuré) | Tier |
|---|---|---|---|---|
| §5.1 Classification + recherche contextuelle | `POST /apex/classify` | message + historique + fragments candidats | `{intention, fragments_pertinents, confiance, langue_detectee}` | fast (Haiku) |
| §5.2 Génération de réponse | `POST /apex/generate` | fragments pertinents + historique + ton + outils actifs | `{texte, confiance, justification, appel_outil_demande}` | reasoning (Sonnet) |
| §5.3 Détection de sentiment & escalade | `POST /apex/escalade` | message + historique + règles + nb échanges | `{sentiment, declencheurs_actifs, tentative_contournement, decision, justification}` | reasoning (Sonnet) |
| §5.4 Sélection d'outil | **fusionné dans `/apex/generate`** (voir ci-dessous) | — | — | — |
| §5.5 Décision de prochaine action | `POST /apex/decide-action` | niveau d'autonomie + intention + confiance + décision d'escalade | `{action, justification}` | **aucun — logique pure** |

**Deux choix de conception à signaler explicitement** (le CDCF liste 5 « points
d'appel à Claude » ; ce service en compte 3 appels LLM réels) :

1. **§5.4 (sélection d'outil) n'est pas un endpoint séparé.** Le CDCF §4.4 dit
   explicitement que la sélection d'outil est décidée « dans le même appel » que
   la génération, « sans appel Claude supplémentaire » — et §5.2 liste
   `appel_outil_demandé` comme une sortie possible de *ce même* prompt. Nous avons
   donc intégré la décision dans `GENERATE_SYSTEM` (`app/prompts/apex.py`) plutôt
   que de dupliquer un appel. Comme ce service ne peut pas exécuter l'outil
   lui-même (c'est n8n, §2.4), le flux reste en deux requêtes HTTP côté
   appelant quand un outil est réellement nécessaire : `/apex/generate` renvoie
   `appel_outil_demande` (texte vide) → n8n exécute l'outil → n8n rappelle
   `/apex/generate` avec `resultat_outil` rempli. Aucun appel Claude
   supplémentaire n'est ajouté par ce choix.
2. **§5.5 (décision finale) est implémenté en logique pure, sans appel LLM.**
   §4.14 donne une table de décision complète et fermée par niveau
   d'autonomie — rien n'y est laissé au jugement d'un modèle. Voir le
   commentaire en tête de `app/apex/autonomie.py` pour le détail du
   raisonnement. **Si un appel Claude dédié était réellement voulu ici,
   merci de le signaler** — c'est une interprétation, pas une réécriture du CDCF.

Toutes les sorties LLM sont en **JSON structuré** — jamais de texte libre.

---

## 7. Garde-fous NON-négociables (critères de recette)

1. 🚫 Jamais suggérer qu'APEX **remplace** un conseiller humain (« augmenter », pas « remplacer »).
2. 🚫 **Règle absolue** : APEX ne répond JAMAIS depuis la connaissance générale du
   modèle. Sans fragment pertinent au-dessus du seuil configuré, **aucune
   réponse n'est générée et une escalade est requise** — vérifié EN PYTHON
   dans `/apex/classify` **et** `/apex/generate` (double vérification, jamais
   laissé à la seule confiance déclarée par Claude ni au seul texte du prompt).
3. 🚫 Aucun outil n'écrit jamais dans le CRM/helpdesk du client sans action
   explicitement prévue par un module (§2.4).
4. 🚫 Un outil désactivé pour un workspace est invisible pour Claude sur ce
   workspace (`outils_actifs`).
5. ✅ Sorties Claude **toujours structurées** (JSON).
6. ✅ Toute ambiguïté (confiance, décision d'escalade) → traitée par défaut
   comme **« escalade »**, jamais comme « continuer ».
7. ✅ Contenu externe (chunks de documents, message du client final) traité
   strictement comme **donnée**, jamais comme instruction (anti-injection, §4.13).
8. ✅ Récidive de tentative de contournement du prompt → escalade automatique (§4.13).
9. ✅ Tout nouveau workspace démarre en **Niveau 1 Supervisé** — jamais autonome sans choix explicite du client (§4.14).
10. ✅ Chaque décision est accompagnée d'une justification courte, prête à devenir un `conversation_events.justification`.
11. ✅ Multilingue sans reprise de code (`langue_detectee` piloté par la sortie du modèle, jamais codé en dur).

---

## 8. Comment le service IA (ce dépôt) implémente APEX

| Fichier | Rôle |
|---|---|
| `app/schemas/apex.py` | contrats Pydantic (I/O) — `Intention`, `NiveauAutonomie`, `ConfigAgentApex`, requêtes/réponses |
| `app/prompts/apex.py` | prompts système (garde-fous non négociables) |
| `app/apex/agents.py` | `classify` / `generate` / `detecter_escalade` — appels Claude via la gateway, garde-fous déterministes appliqués AVANT tout appel LLM |
| `app/apex/autonomie.py` | `decider_action` — logique pure des niveaux d'autonomie (§4.14) |
| `app/apex/config.py` | chargement temporaire de `agent_config` (apex) depuis `samples/configs_apex/*.json` — même mécanisme que `app/builder/config.py` |
| `app/api/v1/endpoints/apex.py` | surface HTTP : `/apex/classify`, `/apex/generate`, `/apex/escalade`, `/apex/decide-action`, `/apex/config/{workspace_id}` |

**Garde-fous déterministes appliqués en Python (jamais laissés au LLM), avec le
même principe que le plafond de relance côté ARES :**
- `/apex/classify` — `necessite_escalade` calculé à partir du **seuil configuré**, jamais de la confiance annoncée par Claude.
- `/apex/generate` — **règle absolue** (« APEX ne doit JAMAIS répondre à partir de ses connaissances générales ») vérifiée EN PYTHON, pas seulement dans le prompt : fragments pertinents vides **OU** tous sous `seuil_pertinence` ⇒ **aucun appel LLM**, `texte=""`, `necessite_escalade=true`, `meta=null`. Ce contrôle est redondant avec celui de `/apex/classify` par construction (défense en profondeur) : même appelé directement, avec des fragments non vides mais faibles, `/apex/generate` refuse de générer.
- `/apex/escalade` — `intention == demande_humain` ou plafond d'échanges atteint ⇒ **escalade déterministe, aucun appel LLM** ; décision imparsable ou récidive de contournement ⇒ escalade forcée après l'appel.
- `/apex/decide-action` — **aucun appel LLM**, table de décision fermée (§4.14).

---

## 9. Cycle de vie complet d'une conversation (§7.A)

```
Réception multicanal (n8n, hors service)
  → POST /apex/classify        (intention + fragments hiérarchisés + necessite_escalade)
  → si necessite_escalade → POST /apex/escalade puis fin (escalade directe, §4.3)
  → sinon → POST /apex/generate  (texte + confiance, ou appel_outil_demande)
      → si appel_outil_demande → n8n exécute l'outil → POST /apex/generate (resultat_outil rempli)
  → POST /apex/escalade          (sentiment, décision continuer/brouillon/escalade)
  → POST /apex/decide-action     (action finale : repondre/brouillon/escalade/cloturer)
  → backend : envoie / met en brouillon / notifie l'équipe / ferme le fil
  → à chaque étape : conversation_events (backend/n8n) → agrégé par AURA
```

---

## 10. Ce que ce service NE fait PAS (frontières)

- ❌ Pas d'accès à pgvector ni à la base de données (Big Data / backend).
- ❌ Pas d'exécution d'outil (CRM, ticketing) — décision uniquement (n8n exécute).
- ❌ Pas de réception multicanal (n8n : Typebot, email, WhatsApp, Slack).
- ❌ Pas de persistance ni d'isolation multi-tenant / RLS (backend).
- ❌ Pas d'agrégats de reporting (AURA).
- ❌ Pas de jeu d'évaluation automatisé avant activation (MVP allégé, tableau de contrôle côté backend, §4.12).

---

## 11. Références
- CDCF APEX v2.0 — « Édition Enrichie » (fichier `CDCF_APEX_VIRELYON_v2_0.docx`, racine du dépôt).
- `docs/ARES.md` — l'agent jumeau, même architecture de service.
- `docs/CONTRAT_API.md` — le contrat HTTP complet (ARES + APEX) pour le backend.
- `../DOCUMENTATION.md` — le service IA en détail.
