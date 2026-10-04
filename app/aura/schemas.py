"""Contrats d'entrée/sortie (API) de la partie IA d'AURA — reporting & analytics.

Même principe que app/schemas/ares.py et apex.py : service STATELESS. Le backend
transmet des données DÉJÀ calculées et validées (KPI, agrégats) ; l'IA les met en
mots, elle ne calcule rien et n'accède jamais à la base. Ces schémas ne portent
AUCUNE logique métier : ils ne font que refuser ce qui est mal formé.

**Zéro n'est pas « manquant »** — règle centrale de ce module :
- une valeur réelle est un nombre (`0` compris) ;
- une valeur indisponible est un objet `DonneeManquante` explicite
  (`{"donnee_manquante": true, "motif": "..."}`) ;
- `null`, booléens et chaînes sont REFUSÉS à la place d'un nombre : aucun
  schéma ne peut donc confondre « 0 lead » et « on ne sait pas ».

Réutilisé (non dupliqué) : `Meta` (app/schemas/common.py) pour les métadonnées
de coût/modèle, et la convention Request / Result / Response+meta de ares.py.
Les validations croisées avec l'entrée (ex. un indicateur cité existe bien dans
la requête) relèvent de la couche de validation des réponses, pas de ces schémas.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Annotated, Literal, Optional, Union
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    model_validator,
)

from app.schemas.common import Meta


class _Contrat(BaseModel):
    """Base stricte : un champ inconnu est une erreur, pas un champ ignoré."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _fini(v):
    if isinstance(v, float) and not math.isfinite(v):
        raise ValueError("NaN et l'infini ne sont pas des valeurs de KPI.")
    return v


# Nombre réel. StrictInt/StrictFloat refusent bool, str et None : `True`, "12"
# ou null ne passent jamais pour une valeur chiffrée.
Nombre = Annotated[Union[StrictInt, StrictFloat], AfterValidator(_fini)]

Slug = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
Texte = Annotated[str, Field(min_length=1, max_length=2000)]
Locale = Annotated[str, Field(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")]  # "fr", "fr-FR"


# ----- Valeur ou donnée manquante (jamais ambiguë) -----
class DonneeManquante(_Contrat):
    """Valeur indisponible. Distincte de 0, qui est une vraie valeur."""

    donnee_manquante: Literal[True]
    motif: Texte  # ex. « source de facturation non connectée »


Mesure = Union[Nombre, DonneeManquante]


class PeriodeAnalysee(_Contrat):
    debut: date
    fin: date

    @model_validator(mode="after")
    def _ordre(self):
        if self.debut > self.fin:
            raise ValueError("`debut` doit précéder ou égaler `fin`.")
        return self


# ----- 1/2. Narration de rapport -----
class IndicateurCalcule(_Contrat):
    """KPI calculé et validé par le backend. `valeur` est OBLIGATOIRE (0 permis)."""

    id: Slug
    libelle: Texte
    valeur: Nombre
    unite: Optional[str] = None  # "%", "EUR", "leads"…
    # Valeur de la période précédente. Absent (None) = aucune comparaison
    # fournie ; indisponible = `DonneeManquante` ; 0 = vraie valeur.
    valeur_precedente: Optional[Mesure] = None


class IndicateurManquant(_Contrat):
    """Indicateur que le backend n'a pas pu fournir — à signaler, jamais à commenter."""

    id: Slug
    libelle: Texte
    motif: Texte


class IndicateurEnErreur(_Contrat):
    """Indicateur dont le calcul a ÉCHOUÉ côté backend (≠ manquant : la donnée
    existe peut-être, mais le calcul a planté). À signaler comme erreur, jamais
    à convertir en valeur numérique."""

    id: Slug
    libelle: Texte
    motif: Texte  # ex. « délai d'attente dépassé lors du calcul »


class NarrativeInput(_Contrat):
    workspace_id: UUID
    objectif_principal: str = ""
    locale: Locale = "fr"
    periode: Optional[PeriodeAnalysee] = None
    indicateurs_calcules: list[IndicateurCalcule] = Field(default_factory=list)
    donnees_manquantes: list[IndicateurManquant] = Field(default_factory=list)
    indicateurs_en_erreur: list[IndicateurEnErreur] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ids_disjoints_et_uniques(self):
        tous = (
            [i.id for i in self.indicateurs_calcules]
            + [i.id for i in self.donnees_manquantes]
            + [i.id for i in self.indicateurs_en_erreur]
        )
        if len(set(tous)) != len(tous):
            raise ValueError(
                "identifiant d'indicateur en double (ou présent dans plusieurs "
                "listes : calculé, manquant, en erreur)."
            )
        return self


class AnalyseIndicateur(_Contrat):
    indicateur_id: Slug
    commentaire: Texte
    tendance: Literal["hausse", "baisse", "stable", "indeterminee"] = "indeterminee"


class NarrativeOutput(_Contrat):
    resume: Texte
    analyses: list[AnalyseIndicateur] = Field(default_factory=list)
    points_attention: list[Texte] = Field(default_factory=list)
    # Recopie des données manquantes reçues : le rapport doit les afficher
    # comme telles, jamais les remplacer par un chiffre.
    donnees_manquantes: list[IndicateurManquant] = Field(default_factory=list)
    # Idem pour les erreurs de calcul : signalées telles quelles.
    indicateurs_en_erreur: list[IndicateurEnErreur] = Field(default_factory=list)


class NarrativeResponse(NarrativeOutput):
    # None quand la réponse est déterministe (aucun appel LLM), comme DecideResponse.
    meta: Optional[Meta] = None


# ----- 3/4. Bulletin de performance ARES / APEX -----
# Dictionnaire de mesures NOMMÉES (le backend peut ajouter un KPI sans changer
# le contrat), mais chaque valeur est typée : nombre ou DonneeManquante.
AgregatsAgent = Annotated[dict[Slug, Mesure], Field(min_length=1)]


class PerformanceInput(_Contrat):
    workspace_id: UUID
    periode: PeriodeAnalysee
    locale: Locale = "fr"
    ares: Optional[AgregatsAgent] = None
    apex: Optional[AgregatsAgent] = None

    @model_validator(mode="after")
    def _au_moins_un_agent(self):
        if self.ares is None and self.apex is None:
            raise ValueError("fournir au moins un agent (`ares` et/ou `apex`).")
        return self


class BulletinAgent(_Contrat):
    synthese: Texte
    points_forts: list[Texte] = Field(default_factory=list)
    points_vigilance: list[Texte] = Field(default_factory=list)
    # Clés d'agrégats effectivement citées (contrôlable côté validation).
    metriques_citees: list[Slug] = Field(default_factory=list)


class PerformanceOutput(_Contrat):
    resume: Texte
    ares: Optional[BulletinAgent] = None
    apex: Optional[BulletinAgent] = None
    recommandations: list[Texte] = Field(default_factory=list)

    @model_validator(mode="after")
    def _au_moins_un_bulletin(self):
        if self.ares is None and self.apex is None:
            raise ValueError("le bulletin doit couvrir au moins un agent.")
        return self


class PerformanceResponse(PerformanceOutput):
    meta: Optional[Meta] = None


# ----- 5/6. Disponibilité d'un indicateur -----
class SourceRequise(_Contrat):
    """Une source dont dépend l'indicateur, telle que le backend la voit."""

    nom: Texte  # ex. "facturation"
    connectee: bool
    champs_requis: list[Texte] = Field(default_factory=list)
    champs_disponibles: list[Texte] = Field(default_factory=list)


class IndicatorAvailabilityInput(_Contrat):
    workspace_id: UUID
    locale: Locale = "fr"  # langue du `motif` renvoyé
    indicateur_id: Slug
    libelle: Optional[Texte] = None
    sources_requises: list[SourceRequise] = Field(min_length=1)


class IndicatorAvailabilityOutput(_Contrat):
    """Format strict : {"disponible": true, "motif": null} ou
    {"disponible": false, "motif": "<raison>"}."""

    disponible: bool
    motif: Optional[Texte] = None

    @model_validator(mode="after")
    def _motif_coherent(self):
        if self.disponible and self.motif is not None:
            raise ValueError("`motif` doit être null quand l'indicateur est disponible.")
        if not self.disponible and self.motif is None:
            raise ValueError("`motif` est obligatoire quand l'indicateur est indisponible.")
        return self


class IndicatorAvailabilityResponse(IndicatorAvailabilityOutput):
    meta: Optional[Meta] = None
