"""Rôles d'agent et capacités associées.

Un workspace n'a pas *un* ARES mais **plusieurs agents nommés**, chacun tenant un
poste précis dans le tunnel. Ce module dit qui a le droit de faire quoi.

La séparation n'est pas décorative. Sans elle, « deux agents » ne serait qu'un
libellé sur l'écran : n'importe lequel pourrait exécuter n'importe quel appel, et
le client n'aurait aucune garantie que son relanceur ne parte pas prospecter.

**Une capacité = un point de décision**, nommé comme l'endpoint qui l'expose.
Ajouter un rôle se fait ici, et nulle part ailleurs.
"""
from __future__ import annotations

# Postes ouverts sur ARES. APEX et AURA auront les leurs, dans leurs modules.
ROLES: tuple[str, ...] = ("prospecteur", "relanceur")

# Capacités reconnues, dans l'ordre du tunnel.
CAPACITES: tuple[str, ...] = (
    "sourcing",   # chercher des entreprises sur les sources externes
    "qualify",    # trancher : ce lead correspond-il à l'ICP ?
    "score",      # noter et rattacher à un palier de relance
    "generate",   # rédiger un message
    "classify",   # lire la réponse du prospect
    "decide",     # continuer, mettre en pause, escalader, arrêter
)

CAPACITES_PAR_ROLE: dict[str, frozenset] = {
    # Amont — trouver, trier, engager la conversation.
    "prospecteur": frozenset({"sourcing", "qualify", "score", "generate"}),
    # Aval — entretenir une conversation déjà ouverte jusqu'à l'escalade humaine.
    # Il garde `score` : la cadence de relance dépend du palier du lead (§4.3.1),
    # qu'il doit pouvoir recalculer après un nouvel événement.
    "relanceur": frozenset({"score", "generate", "classify", "decide"}),
}

# `generate` est volontairement partagé : le prospecteur écrit le premier contact,
# le relanceur les suivants. C'est le même acte de rédaction, à deux étapes de la
# séquence — les séparer créerait deux prompts à maintenir pour un seul métier.

# Nom affiché et mission par défaut, pour un agent que le client n'a pas encore
# personnalisé. La mission part dans le prompt de génération : la laisser vide
# donnerait des messages interchangeables d'un client à l'autre.
PROFILS_PAR_DEFAUT: dict[str, tuple[str, str]] = {
    "prospecteur": (
        "Prospecteur",
        "Identifier des entreprises correspondant à l'ICP et engager le premier contact.",
    ),
    "relanceur": (
        "Relanceur",
        "Entretenir la conversation ouverte jusqu'à la reprise en main par un commercial.",
    ),
}


def capacites(role: str) -> frozenset:
    """Capacités d'un rôle. Un rôle inconnu n'a aucun droit, plutôt que tous."""
    return CAPACITES_PAR_ROLE.get(role, frozenset())


def peut(role: str, capacite: str) -> bool:
    return capacite in capacites(role)
