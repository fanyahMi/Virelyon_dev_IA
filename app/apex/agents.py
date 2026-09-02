"""Points de décision d'APEX appelant Claude (via la passerelle).

Mêmes principes que app/ares/agents.py : chaque fonction construit l'entrée
JSON, appelle le bon tier, valide la sortie structurée, joint les métadonnées
(modèle, tokens, coût). `dump_json`, `requis` et `meta_depuis` sont réutilisés
tels quels depuis ares/agents.py — même précédent que app/builder/icp.py, qui
les importe déjà (aucun code dupliqué entre agents).

Garde-fous non négociables du CDCF APEX v2.0 : ils sont appliqués ICI, en
Python, jamais laissés au seul jugement du modèle — même logique que le
plafond de relance déterministe côté ARES (`ares/agents.py::decide`) :
- anti-hallucination (§4.3/§4.4) : aucun fragment pertinent => aucun appel LLM,
  jamais de réponse générée depuis la connaissance générale de Claude ;
- ambiguïté = escalade par défaut (§0/§4.5) : une décision d'escalade
  imparsable retombe sur "escalade", jamais sur "continuer" ;
- priorité absolue (§4.5) : demande humaine explicite ou plafond d'échanges
  atteint déclenchent une escalade déterministe, sans appel LLM.
"""
from app.ares.agents import dump_json, meta_depuis, requis
from app.gateway.router import Gateway
from app.prompts.apex import CLASSIFY_SYSTEM, ESCALADE_SYSTEM, GENERATE_SYSTEM
from app.schemas.apex import (
    AppelOutilDemande,
    ClassifyRequest,
    ClassifyResponse,
    DecisionEscalade,
    DetecterEscaladeRequest,
    DetecterEscaladeResponse,
    FragmentPertinent,
    GenerateRequest,
    GenerateResponse,
    Intention,
    Sentiment,
    TypeRegleEscalade,
)

# Plafond d'échanges sans résolution appliqué quand le workspace n'a configuré
# aucune règle `nb_echanges_sans_resolution` — « valeurs par défaut prudentes
# appliquées, jamais aucune règle active » (CDCF §4.5, États & limites).
DEFAULT_NB_ECHANGES_PLAFOND = 3

_DECISION_ALIASES = {
    "continuer": DecisionEscalade.continuer,
    "brouillon": DecisionEscalade.brouillon,
    "escalade": DecisionEscalade.escalade,
}

_SENTIMENT_ALIASES = {
    "positif": Sentiment.positif,
    "neutre": Sentiment.neutre,
    "negatif": Sentiment.negatif,
    "négatif": Sentiment.negatif,
}


def _plafond_echanges(req: DetecterEscaladeRequest) -> int:
    """Plafond configuré par le client, ou le défaut prudent (CDCF §4.5)."""
    for regle in req.regles_escalade:
        if regle.actif and regle.type_regle == TypeRegleEscalade.nb_echanges_sans_resolution:
            try:
                return int(regle.valeur_seuil.get("seuil", DEFAULT_NB_ECHANGES_PLAFOND))
            except (TypeError, ValueError):
                return DEFAULT_NB_ECHANGES_PLAFOND
    return DEFAULT_NB_ECHANGES_PLAFOND


# ----- §4.3 / prompt §5.1 -----
async def classify(gw: Gateway, req: ClassifyRequest) -> ClassifyResponse:
    user = dump_json(
        {
            "message": req.message_entrant,
            "historique": [h.model_dump(mode="json") for h in req.historique],
            "fragments_candidats": [f.model_dump(mode="json") for f in req.fragments_candidats],
            "langue_workspace": req.langue_workspace,
        }
    )
    data, info = await gw.complete_json("fast", CLASSIFY_SYSTEM, user, req.workspace_id)

    intention = Intention(str(requis(data, "intention")))
    fragments_bruts = data.get("fragments_pertinents") or []
    fragments = [
        FragmentPertinent(chunk_texte=str(f.get("chunk_texte", "")), score=float(f.get("score", 0.0)))
        for f in fragments_bruts
        if isinstance(f, dict)
    ]
    # Garde-fou anti-hallucination (§4.3) : décision calculée en Python à
    # partir du seuil CONFIGURÉ, jamais laissée à la confiance déclarée par
    # Claude — « score de pertinence ambigu = traité par défaut comme
    # insuffisant » (§4.3, États & limites).
    necessite_escalade = not fragments or max(f.score for f in fragments) < req.seuil_pertinence

    return ClassifyResponse(
        intention=intention,
        fragments_pertinents=fragments,
        confiance=float(requis(data, "confiance")),
        langue_detectee=str(data.get("langue_detectee") or req.langue_workspace),
        necessite_escalade=necessite_escalade,
        meta=meta_depuis(info),
    )


# ----- §4.4 / prompts §5.2 + §5.4 (sélection d'outil intégrée) -----
async def generate(gw: Gateway, req: GenerateRequest) -> GenerateResponse:
    # RÈGLE ABSOLUE, vérifiée ICI EN PYTHON — jamais seulement dans le prompt :
    # « APEX ne doit JAMAIS répondre à partir de ses connaissances générales ».
    # Sans fragment pertinent AU-DESSUS DU SEUIL configuré, aucun appel LLM
    # n'est fait et aucune réponse n'est générée — escalade requise (§4.3/§4.4,
    # critère de recette). Défense en profondeur avec /apex/classify (qui
    # applique déjà ce même calcul) : ce endpoint reste sûr même appelé
    # directement, avec des fragments non vides mais tous sous le seuil.
    if not req.fragments_pertinents or max(f.score for f in req.fragments_pertinents) < req.seuil_pertinence:
        return GenerateResponse(
            texte="",
            confiance=0.0,
            justification=(
                "Aucun contenu suffisamment pertinent dans la base de connaissances "
                "(seuil de pertinence non atteint) — garde-fou anti-hallucination "
                "(CDCF APEX §4.3/§4.4), appliqué en Python : aucun appel LLM, "
                "escalade vers un humain requise."
            ),
            necessite_escalade=True,
            appel_outil_demande=None,
            meta=None,
        )

    user = dump_json(
        {
            "fragments_pertinents": [f.model_dump(mode="json") for f in req.fragments_pertinents],
            "historique": [h.model_dump(mode="json") for h in req.historique],
            "ton_de_voix": req.ton_de_voix,
            "langue": req.langue,
            "outils_actifs": req.outils_actifs,
            "resultat_outil": req.resultat_outil.model_dump(mode="json") if req.resultat_outil else None,
        }
    )
    data, info = await gw.complete_json(
        "reasoning", GENERATE_SYSTEM, user, req.workspace_id, max_tokens=1500
    )

    appel_brut = data.get("appel_outil_demande")
    appel_outil = None
    if isinstance(appel_brut, dict) and appel_brut.get("nom_outil"):
        appel_outil = AppelOutilDemande(
            nom_outil=str(appel_brut["nom_outil"]),
            parametres=dict(appel_brut.get("parametres") or {}),
        )
        # Défense en profondeur : si un resultat_outil était déjà fourni,
        # Claude ne doit plus en redemander un — on ignore une éventuelle
        # redemande plutôt que de risquer une boucle avec n8n.
        if req.resultat_outil is not None:
            appel_outil = None

    return GenerateResponse(
        texte=str(data.get("texte", "") or ""),
        confiance=float(requis(data, "confiance")),
        justification=str(data.get("justification", "")),
        necessite_escalade=False,  # le garde-fou ci-dessus a déjà validé les fragments
        appel_outil_demande=appel_outil,
        meta=meta_depuis(info),
    )


# ----- §4.5 / prompt §5.3 -----
async def detecter_escalade(gw: Gateway, req: DetecterEscaladeRequest) -> DetecterEscaladeResponse:
    # Règles déterministes « priorité absolue » (§4.5, table Déclencheur →
    # Décision → Résultat) — jamais laissées au jugement du modèle, jamais
    # d'appel LLM inutile.
    if req.intention == Intention.demande_humain:
        return DetecterEscaladeResponse(
            decision=DecisionEscalade.escalade,
            declencheurs_actifs=[],
            justification="Demande explicite d'un humain — priorité absolue sur toute autre règle (§4.5).",
            meta=None,
        )

    plafond = _plafond_echanges(req)
    if req.nb_echanges_sans_resolution >= plafond:
        return DetecterEscaladeResponse(
            decision=DecisionEscalade.escalade,
            declencheurs_actifs=[TypeRegleEscalade.nb_echanges_sans_resolution],
            justification=(
                f"Plafond d'échanges sans résolution atteint "
                f"({req.nb_echanges_sans_resolution}/{plafond}) — escalade automatique."
            ),
            meta=None,
        )

    user = dump_json(
        {
            "message": req.message_entrant,
            "historique": [h.model_dump(mode="json") for h in req.historique],
            "intention": req.intention.value if req.intention else None,
            "regles_escalade": [r.model_dump(mode="json") for r in req.regles_escalade],
            "nb_echanges_sans_resolution": req.nb_echanges_sans_resolution,
        }
    )
    data, info = await gw.complete_json("reasoning", ESCALADE_SYSTEM, user, req.workspace_id)

    sentiment = _SENTIMENT_ALIASES.get(str(data.get("sentiment", "")).strip().lower(), Sentiment.neutre)
    decision = _DECISION_ALIASES.get(str(requis(data, "decision")).strip().lower())
    if decision is None:
        # Ambiguïté sur la décision elle-même : jamais "continuer" par défaut
        # (CDCF §0/§4.5 — la prudence par défaut est un critère de recette).
        decision = DecisionEscalade.escalade

    tentative = bool(data.get("tentative_contournement", False))
    declencheurs: list[TypeRegleEscalade] = []
    for d in data.get("declencheurs_actifs") or []:
        try:
            declencheurs.append(TypeRegleEscalade(str(d)))
        except ValueError:
            continue  # valeur hors référentiel renvoyée par le modèle : ignorée, jamais inventée

    justification = str(data.get("justification", ""))
    # Récidive de contournement = escalade non négociable, quelle que soit la
    # décision annoncée par le modèle (§4.13).
    if tentative and req.tentatives_contournement_precedentes >= 1:
        decision = DecisionEscalade.escalade
        justification = (
            "Récidive de tentative de contournement détectée — escalade automatique (§4.13). "
            + justification
        )

    return DetecterEscaladeResponse(
        sentiment=sentiment,
        declencheurs_actifs=declencheurs,
        tentative_contournement=tentative,
        decision=decision,
        justification=justification,
        meta=meta_depuis(info),
    )
