"""Décision finale de prochaine action — niveaux de confiance opérationnelle
(CDCF APEX v2.0 §4.14, transverse §4.5/§4.6, contrat §5.5).

**Choix de conception à signaler** : le CDCF liste ce point sous "5.
Spécification des prompts systèmes", ce qui pourrait suggérer un 5e appel
Claude dédié. Nous l'implémentons ici en logique PURE (aucun appel LLM), pour
deux raisons tirées du document lui-même :
1. §4.14 donne une table de décision COMPLÈTE et FERMÉE par niveau
   d'autonomie (Supervisé/Semi-autonome/Autonome) — rien n'y est laissé au
   jugement d'un modèle : c'est une politique de configuration, pas un
   jugement contextuel (contrairement à `ares/agents.py::decide`, où Claude
   évalue une intention réelle de prospect).
2. Cohérence avec la discipline de coût affirmée ailleurs dans le CDCF
   (§4.4 : "auto-évaluation ... sans appel Claude supplémentaire").

Si une évaluation par Claude était réellement voulue pour ce point précis
(au-delà de la combinaison déterministe ci-dessous), merci de le confirmer :
c'est un écart d'interprétation possible, pas une réécriture du CDCF.
"""
from app.schemas.apex import (
    ActionAgent,
    DecideActionRequest,
    DecideActionResponse,
    DecisionEscalade,
    Intention,
    NiveauAutonomie,
)


def decider_action(req: DecideActionRequest) -> DecideActionResponse:
    # Un humain a explicitement clôturé le fil : l'emporte sur tout (§4.6).
    if req.cloture_demandee:
        return DecideActionResponse(
            action=ActionAgent.cloturer,
            justification="Conversation marquée résolue par un humain (§4.6).",
        )

    # Escalade décidée par le Module 4.5 : jamais contournée, quel que soit le
    # niveau d'autonomie — priorité absolue (§4.5).
    if req.decision_escalade == DecisionEscalade.escalade:
        return DecideActionResponse(
            action=ActionAgent.escalade,
            justification="Escalade déclenchée par le Module 4.5 (détection de confiance).",
        )

    # Niveau 1 — Supervisé (valeur par défaut) : APEX rédige mais n'envoie
    # JAMAIS seul (§4.14).
    if req.niveau_autonomie == NiveauAutonomie.supervise:
        return DecideActionResponse(
            action=ActionAgent.brouillon,
            justification=(
                "Niveau Supervisé : toute réponse reste en brouillon en attente "
                "de validation humaine (§4.14)."
            ),
        )

    # Le Module 4.5 a déjà déclassé la réponse en brouillon (confiance
    # ambiguë) : s'applique quel que soit le niveau au-dessus de Supervisé.
    if req.decision_escalade == DecisionEscalade.brouillon:
        return DecideActionResponse(
            action=ActionAgent.brouillon,
            justification=(
                "Confiance jugée insuffisante par le Module 4.5 — passage en "
                "brouillon plutôt qu'envoi automatique."
            ),
        )

    # Niveau 2 — Semi-autonome : répond seul uniquement sur une intention
    # question_produit à confiance élevée ; réclamation/hors-scope restent en
    # brouillon (§4.14).
    if req.niveau_autonomie == NiveauAutonomie.semi_autonome:
        if req.intention == Intention.question_produit and req.confiance >= req.seuil_confiance_semi_autonome:
            return DecideActionResponse(
                action=ActionAgent.repondre,
                justification=(
                    "Niveau Semi-autonome : intention question_produit à "
                    "confiance élevée — envoi automatique."
                ),
            )
        return DecideActionResponse(
            action=ActionAgent.brouillon,
            justification=(
                "Niveau Semi-autonome : intention ou confiance insuffisante "
                "pour un envoi automatique — brouillon."
            ),
        )

    # Niveau 3 — Autonome : répond sur toute intention, sauf règle d'escalade
    # déjà écartée ci-dessus (§4.14).
    return DecideActionResponse(
        action=ActionAgent.repondre,
        justification="Niveau Autonome : aucune règle d'escalade déclenchée — envoi automatique.",
    )
