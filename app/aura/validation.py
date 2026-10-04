"""Garde-fou anti-hallucination des TEXTES LIBRES produits par Claude.

AURA ne calcule rien : tout chiffre-KPI écrit dans une narration ou un bulletin
doit être, EXACTEMENT, la valeur fournie par le backend pour CET indicateur, avec
la bonne unité. Fonctions pures, déterministes, sans LLM ni accès externe.

Méthode, phrase par phrase :
1. **Normalisation** (1 caractère → 1 caractère : minuscules, accents ôtés).
2. **Nombres** : chiffres (`15000`, `15 000`, `15.000`, `15,5`), échelles
   explicites (`15k`, `15 K`, `15 mille`, `2 millions`) et nombres écrits en toutes
   lettres français/anglais (`quinze mille`, `vingt-cinq`, `twenty five`). Une
   forme est ÉQUIVALENTE si — et seulement si — elle vaut exactement le même
   nombre. Aucune interprétation approximative : `environ 15k`, `plus de 15 000`,
   `~15000`, `estimé à…` sont des approximations, jamais des valeurs exactes.
3. **Nombres contextuels exemptés** : année (2026), trimestre (T3, Q3, « 1er
   trimestre »), rang (« top 3 »), ordinaux (1er, 3e), dates, durées, décomptes
   d'éléments (« 3 indicateurs »). Tant qu'ils ne portent pas une unité monétaire
   ou « % », ils ne sont pas des valeurs de KPI et ne sont pas contrôlés.
4. **Rattachement** : chaque autre nombre est rattaché à l'indicateur mentionné
   dans la phrase (libellé, identifiant, sigle ou mot distinctif ; pluriel toléré) :
   « 12 clients » → à ce qui suit ; sinon la mention qui précède le plus
   près ; sinon l'indicateur de l'analyse en cours. Il doit alors égaler SA valeur
   (ou sa valeur précédente si la phrase compare), avec une unité compatible.
   Un nombre rattaché à un indicateur manquant ou en erreur est interdit : le zéro
   d'un autre indicateur ne peut jamais le valider.
5. **Non rattaché** : un nombre exprimé en monnaie ou en % doit égaler la valeur
   d'un indicateur calculé de cette unité ; tout autre nombre doit figurer parmi
   les chiffres fournis.

Limites assumées : mentions par mots (une paraphrase sans libellé, sigle ni mot
distinctif n'est pas rattachée) ; nombres en toutes lettres : français et anglais
seulement (les chiffres sont, eux, contrôlés quelle que soit la langue) ; mots du
contexte (trimestre, mois…) : français et anglais.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Literal, Optional

Statut = Literal["calcule", "manquant", "erreur"]


@dataclass(frozen=True)
class IndicateurRef:
    """Ce que le backend a fourni pour un indicateur — la seule vérité."""

    cle: str                              # identifiant unique ("ca", "ares.leads_generes")
    libelle: str
    statut: Statut
    valeur: Optional[float] = None        # None si manquant ou en erreur
    precedentes: tuple[float, ...] = ()
    unite: Optional[str] = None


# ----------------------------------------------------------------- normalisation
def normaliser(texte: str) -> str:
    """Minuscules sans accents, longueur INCHANGÉE (les positions restent valides)."""
    sortie = []
    for c in texte:
        d = unicodedata.normalize("NFD", c)
        b = d[0] if d else c
        if b in "   ":
            b = " "
        elif b in "’‘`´":
            b = "'"
        bas = b.lower()
        sortie.append(bas if len(bas) == 1 else b)
    return "".join(sortie)


# ----------------------------------------------------------------- nombres
_ECHELLES = {
    "k": 1e3, "mille": 1e3, "thousand": 1e3,
    "million": 1e6, "millions": 1e6,
    "milliard": 1e9, "milliards": 1e9, "billion": 1e9, "billions": 1e9,
}
_CHIFFRES = re.compile(
    r"(?<![\d.,])(?P<n>\d{1,3}(?: \d{3})+(?:[.,]\d+)?|\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"(?:\s?(?P<e>k|mille|thousand|millions?|milliards?|billions?)(?![a-z]))?"
)
_GROUPES_MILLIERS = re.compile(r"\d{1,3}(?:[ .,]\d{3})+")

_UNITES_MOTS = {
    "zero": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6,
    "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13,
    "quatorze": 14, "quinze": 15, "seize": 16, "vingt": 20, "vingts": 20, "trente": 30,
    "quarante": 40, "cinquante": 50, "soixante": 60, "septante": 70, "octante": 80,
    "huitante": 80, "nonante": 90,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90,
}
_CENTAINES = {"cent", "cents", "hundred"}
_MILLE = {"mille", "thousand"}
_MILLIONS = {"million": 1e6, "millions": 1e6, "milliard": 1e9, "milliards": 1e9,
             "billion": 1e9, "billions": 1e9}
_MOTS_NOMBRE = set(_UNITES_MOTS) | _CENTAINES | _MILLE | set(_MILLIONS)
_ARTICLES_SEULS = {"un", "une", "one"}


@dataclass(frozen=True)
class Nombre:
    debut: int
    fin: int
    texte: str
    valeurs: frozenset  # interprétations numériques EXACTES possibles
    brut: bool          # écrit en chiffres sans séparateur ni échelle (candidat « année »)


def _interpretations(jeton: str) -> set[float]:
    """« 1.500 » vaut 1500 (fr) ou 1,5 (en) : les deux lectures sont admises."""
    if not any(c in jeton for c in " .,"):
        return {float(jeton)}
    valeurs: set[float] = set()
    if _GROUPES_MILLIERS.fullmatch(jeton):
        valeurs.add(float(re.sub(r"[ .,]", "", jeton)))
    dernier = max((i for i, c in enumerate(jeton) if c in ".,"), default=-1)
    if dernier >= 0:
        entier = re.sub(r"[ .,]", "", jeton[:dernier])
        valeurs.add(float(f"{entier}.{jeton[dernier + 1:]}"))
    return valeurs


def _valeur_en_lettres(mots: list[str]) -> float:
    # « quatre-vingt(s) » = 80 (4 × 20), les autres dizaines s'additionnent.
    fusion: list = []
    i = 0
    while i < len(mots):
        if mots[i] == "quatre" and i + 1 < len(mots) and mots[i + 1] in ("vingt", "vingts"):
            fusion.append(80)
            i += 2
        else:
            fusion.append(mots[i])
            i += 1
    total, courant = 0.0, 0.0
    for m in fusion:
        if isinstance(m, int):
            courant += m
        elif m in _CENTAINES:
            courant = (courant or 1) * 100
        elif m in _MILLE:
            total += (courant or 1) * 1000
            courant = 0
        elif m in _MILLIONS:
            total += (courant or 1) * _MILLIONS[m]
            courant = 0
        elif m in ("et", "and"):
            continue
        else:
            courant += _UNITES_MOTS[m]
    return total + courant


def _nombres_en_lettres(norm: str) -> list[Nombre]:
    jetons = [(m.start(), m.end(), m.group(0)) for m in re.finditer(r"[a-z]+", norm)]
    # « pour cent » / « per cent » = pourcentage, pas le nombre 100.
    exclus: set[int] = set()
    for i in range(len(jetons) - 1):
        if jetons[i][2] in ("pour", "per") and jetons[i + 1][2] == "cent":
            exclus.add(i + 1)
    resultats: list[Nombre] = []
    i = 0
    while i < len(jetons):
        if jetons[i][2] not in _MOTS_NOMBRE or i in exclus:
            i += 1
            continue
        j = i
        while j + 1 < len(jetons) and (j + 1) not in exclus:
            ecart = norm[jetons[j][1]:jetons[j + 1][0]]
            suivant = jetons[j + 1][2]
            if not re.fullmatch(r"[\s\-]+", ecart):
                break
            if suivant in _MOTS_NOMBRE:
                j += 1
            elif (suivant in ("et", "and") and j + 2 < len(jetons)
                  and jetons[j + 2][2] in _MOTS_NOMBRE
                  and re.fullmatch(r"[\s\-]+", norm[jetons[j + 1][1]:jetons[j + 2][0]])):
                j += 2
            else:
                break
        mots = [t[2] for t in jetons[i:j + 1]]
        if not (len(mots) == 1 and mots[0] in _ARTICLES_SEULS):  # « un indicateur » ≠ 1
            resultats.append(Nombre(jetons[i][0], jetons[j][1], norm[jetons[i][0]:jetons[j][1]],
                                    frozenset({_valeur_en_lettres(mots)}), False))
        i = j + 1
    return resultats


def extraire_nombres(norm: str) -> list[Nombre]:
    """Tous les nombres d'un texte normalisé (chiffres, échelles, toutes lettres)."""
    trouves: list[Nombre] = []
    for m in _CHIFFRES.finditer(norm):
        base = _interpretations(m.group("n"))
        mult = _ECHELLES[m.group("e")] if m.group("e") else 1
        trouves.append(Nombre(m.start(), m.end(), m.group(0).strip(),
                              frozenset(v * mult for v in base),
                              m.group("n").isdigit() and not m.group("e")))
    # « 15 mille » : l'échelle est déjà absorbée par le nombre en chiffres.
    trouves += [w for w in _nombres_en_lettres(norm)
                if not any(w.debut < t.fin and t.debut < w.fin for t in trouves)]
    return sorted(trouves, key=lambda n: n.debut)


def nombres_dans_les_textes(textes: Iterable[str]) -> list[float]:
    """Nombres écrits dans des textes FOURNIS par le backend (objectif, libellés,
    motifs) : les citer n'est pas une invention."""
    return [v for t in textes for n in extraire_nombres(normaliser(t)) for v in n.valeurs]


# ----------------------------------------------------------------- unités
_MONNAIES = {"€": "eur", "eur": "eur", "euro": "eur", "euros": "eur",
             "$": "usd", "usd": "usd", "dollar": "usd", "dollars": "usd",
             "£": "gbp", "gbp": "gbp"}
_POURCENT = {"%", "pourcent", "pourcents", "percent", "pct"}
_UNITES_KPI = {"eur", "usd", "gbp", "%"}


def _canon_unite(brut: str) -> str:
    u = normaliser(brut).strip()
    if u in _MONNAIES:
        return _MONNAIES[u]
    if u in _POURCENT:
        return "%"
    return u[:-1] if len(u) > 3 and u.endswith("s") else u


def _unite_suivante(norm: str, fin: int) -> Optional[str]:
    reste = norm[fin:]
    if re.match(r"\s*(?:pour|per)\s+cent(?![a-z])", reste):
        return "%"
    m = re.match(r"\s*(€|\$|£|%|[a-z]+)", reste)
    return _canon_unite(m.group(1)) if m else None


# ----------------------------------------------------------------- contexte (exemptions)
_MOIS = {"janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout", "septembre",
         "octobre", "novembre", "decembre", "janv", "fevr", "avr", "juil", "sept", "oct", "nov",
         "dec", "january", "february", "march", "april", "june", "july", "august", "september",
         "october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep"}
_AVANT_CONTEXTE = {
    "top", "trimestre", "trim", "quarter", "semestre", "semester", "semaine", "week", "jour",
    "day", "mois", "month", "annee", "year", "version", "v", "etape", "step", "phase", "niveau",
    "level", "palier", "tier", "page", "section", "chapitre", "chapter", "numero", "no", "n",
    "rang", "classement", "rank", "lot", "fy", "exercice",
}
_APRES_CONTEXTE = {
    "er", "ere", "eme", "ieme", "e", "nd", "rd", "th", "st", "trimestre", "trimestres",
    "quarter", "quarters", "semestre", "semestres", "semaine", "semaines", "week", "weeks",
    "jour", "jours", "day", "days", "mois", "month", "months", "an", "ans", "annee", "annees",
    "year", "years", "heure", "heures", "hour", "hours", "minute", "minutes", "seconde",
    "secondes",
} | _MOIS | {
    "indicateur", "indicateurs", "indicator", "indicators", "donnee", "donnees", "data",
    "element", "elements", "item", "items", "agent", "agents", "source", "sources", "mesure",
    "mesures", "agregat", "agregats", "metrique", "metriques", "metric", "metrics", "kpi",
    "kpis", "point", "points", "recommandation", "recommandations", "rapport", "rapports",
    "report", "reports", "periode", "periodes", "period", "periods", "champ", "champs",
}


def _contextuel(norm: str, n: Nombre, unite: Optional[str]) -> bool:
    """Année, trimestre, rang, ordinal, date, durée, décompte : pas une valeur de KPI."""
    if unite in _UNITES_KPI:
        return False  # « 12 EUR », « 40 % » : présenté comme une valeur
    avant, apres = norm[:n.debut], norm[n.fin:]
    if re.search(r"[a-z]$", avant) or re.search(r"[#°]$", avant):            # T3, Q3, S35, v2, n°3
        return True
    if re.match(r"(?:er|ere|eme|ieme|e|nd|rd|th|st)(?![a-z])", apres):    # 1er, 3e, 4th
        return True
    if re.search(r"\d[-/]$", avant) or re.match(r"[-/]\d", apres):          # 2026-09-01, 01/09
        return True
    precedent = re.search(r"([a-z]+)[\s.:\-]*$", avant)
    if precedent and precedent.group(1) in _AVANT_CONTEXTE:
        return True
    suivant = re.match(r"\s*([a-z]+)", apres)
    if suivant and suivant.group(1) in _APRES_CONTEXTE:
        return True
    if n.brut and len(n.texte) == 4 and 1900 <= next(iter(n.valeurs)) <= 2100:  # année
        return True
    return False


_APPROXIMATION = re.compile(
    r"(?<![a-z])(?:environ|approximativement|approximatif|approximative|approx|"
    r"aux alentours de|autour de|pres de|presque|quasi(?:ment)?|a peu pres|de l'ordre de|"
    r"plus ou moins|plus de|moins de|au moins|au plus|estime(?:e|s|es)?|estimation|about|"
    r"around|roughly|approximately|nearly|almost|circa|more than|less than|at least|"
    r"at most|over|under|estimated)(?![a-z])|[~≈]"
)
_APPROX_APRES = re.compile(
    r"\s*(?:€|\$|£|%|eur\w*|usd|dollars?)?\s*(?:environ|approx\w*|roughly|or so|a peu pres)(?![a-z])"
)
_COMPARAISON = re.compile(
    r"contre|versus|(?<![a-z])vs(?![a-z])|precedent|auparavant|avant|anterieur|passe (?:de|a)|"
    r"evolu|progress|hausse|baisse|augment|diminu|recul|croissance|variation|increas|decreas|"
    r"grew|fell|rose|dropped|previous|prior|(?<![a-z])last(?![a-z])|(?<![a-z])from(?![a-z])|compar"
)


def _approximatif(phrase: str, n: Nombre) -> bool:
    fenetre = phrase[max(0, n.debut - 28):n.debut]
    return bool(_APPROXIMATION.search(fenetre) or _APPROX_APRES.match(phrase[n.fin:n.fin + 16]))


# ----------------------------------------------------------------- mentions d'indicateurs
_MOTS_VIDES = frozenset(
    "de du des la le les un une et en par pour sur avec sans au aux d l a the of and per for to "
    "in on with by".split()
)
_GENERIQUES = frozenset(
    "nouveau nouveaux nouvelle nouvelles new total totaux totale globale global moyen moyenne "
    "average nombre nb number count rate".split()
)


def _mots(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s)


def _alias(ind: IndicateurRef) -> set[tuple[str, ...]]:
    """Façons de désigner un indicateur : libellé, identifiant, sigle, mot distinctif."""
    lib = tuple(_mots(normaliser(ind.libelle)))
    ident = tuple(_mots(normaliser(ind.cle.split(".")[-1])))
    alias = {a for a in (lib, ident) if a}
    utiles = [m for m in lib if m not in _MOTS_VIDES]
    if len(utiles) >= 2:
        alias.add(("".join(m[0] for m in utiles),))      # « Chiffre d'affaires » → « ca »
    for m in set(utiles) | set(ident):
        if len(m) >= 4 and m not in _MOTS_VIDES and m not in _GENERIQUES:
            alias.add((m,))
    return {a for a in alias if len("".join(a)) >= 2}


def _motif_alias(mots: tuple[str, ...]) -> re.Pattern:
    def mot(w: str) -> str:
        racine = w[:-1] if len(w) > 3 and w[-1] in "sx" else w
        return re.escape(racine) + "[sx]?"
    return re.compile(r"(?<![a-z0-9])" + r"[\s'\-_]+".join(mot(w) for w in mots) + r"(?![a-z0-9])")


@dataclass(frozen=True)
class _Mention:
    debut: int
    fin: int
    cles: frozenset


def _construire_aliases(indicateurs: list[IndicateurRef]) -> list[tuple[re.Pattern, frozenset]]:
    table: dict[tuple[str, ...], set[str]] = {}
    for ind in indicateurs:
        for a in _alias(ind):
            table.setdefault(a, set()).add(ind.cle)
    return [(_motif_alias(a), frozenset(c)) for a, c in table.items()]


def _mentions(phrase: str, aliases) -> list[_Mention]:
    brutes = [_Mention(m.start(), m.end(), cles)
              for motif, cles in aliases for m in motif.finditer(phrase)]
    retenues: list[_Mention] = []
    for m in sorted(brutes, key=lambda x: (-(x.fin - x.debut), x.debut)):  # le plus long d'abord
        recouvre = next((r for r in retenues if m.debut < r.fin and r.debut < m.fin), None)
        if recouvre is None:
            retenues.append(m)
        elif (recouvre.debut, recouvre.fin) == (m.debut, m.fin):          # même mot, sens multiples
            retenues[retenues.index(recouvre)] = _Mention(m.debut, m.fin, recouvre.cles | m.cles)
    return sorted(retenues, key=lambda x: x.debut)


_ECART_QUANTIFIEUR = re.compile(
    r"\s*(?:€|\$|£|%|eur|euros?|usd|dollars?)?\s*(?:(?:de|des|du|d')\s*(?:la\s+|l')?)?"
)


def _rattacher(phrase: str, n: Nombre, mentions: list[_Mention], defaut: Optional[str]):
    suivantes = [m for m in mentions if m.debut >= n.fin]
    if suivantes and _ECART_QUANTIFIEUR.fullmatch(phrase[n.fin:suivantes[0].debut]):
        return suivantes[0].cles                      # « 12 nouveaux clients », « 15 000 EUR de CA »
    precedentes = [m for m in mentions if m.fin <= n.debut]
    if precedentes:
        return precedentes[-1].cles                   # « Le CA est de 12 EUR »
    if suivantes:
        return suivantes[0].cles
    return frozenset({defaut}) if defaut else None


# ----------------------------------------------------------------- vérification
def _egal(valeurs: Iterable[float], cibles: Iterable[float]) -> bool:
    cibles = list(cibles)
    return any(math.isclose(v, c, rel_tol=1e-9, abs_tol=1e-9) for v in valeurs for c in cibles)


def _extrait(phrase: str, n: Nombre) -> str:
    return phrase[max(0, n.debut - 25):n.fin + 25].strip()


def verifier_texte(
    texte: str,
    indicateurs: list[IndicateurRef],
    *,
    defaut: Optional[str] = None,
    autorises: Iterable[float] = (),
) -> list[str]:
    """Incohérences entre `texte` et les données du backend ([] = conforme).

    `defaut` : indicateur traité par ce texte (commentaire d'une analyse), utilisé
    quand la phrase n'en nomme aucun. `autorises` : chiffres fournis hors indicateurs
    (période, décomptes, nombres des textes du backend) pour les nombres non rattachés.
    """
    refs = {i.cle: i for i in indicateurs}
    aliases = _construire_aliases(indicateurs)
    unites_connues = {_canon_unite(i.unite) for i in indicateurs if i.unite}
    autorises = list(autorises)
    problemes: list[str] = []

    for phrase in re.split(r"(?<=[.!?;])\s+|\n+|\s[—–]\s", normaliser(texte)):
        nombres = extraire_nombres(phrase)
        if not nombres:
            continue
        mentions = _mentions(phrase, aliases)
        compare = bool(_COMPARAISON.search(phrase))
        for n in nombres:
            unite = _unite_suivante(phrase, n.fin)
            if _contextuel(phrase, n, unite):
                continue
            approx = _approximatif(phrase, n)
            cles = _rattacher(phrase, n, mentions, defaut)

            if cles is None:  # nombre sans indicateur nommé
                if unite in _UNITES_KPI:
                    ok = any(i.statut == "calcule"
                             and (i.unite is None or _canon_unite(i.unite) == unite)
                             and _egal(n.valeurs, [i.valeur, *(i.precedentes if compare else ())])
                             for i in indicateurs) or _egal(n.valeurs, autorises)  # ex. cible de l'objectif
                    raison = f"valeur en {unite} qui ne correspond à aucun indicateur calculé"
                else:
                    ok = _egal(n.valeurs, autorises) or any(
                        i.statut == "calcule" and _egal(n.valeurs, [i.valeur, *i.precedentes])
                        for i in indicateurs)
                    raison = "chiffre absent des données fournies"
                if approx:
                    ok, raison = False, "approximation présentée comme un chiffre"
                if not ok:
                    problemes.append(f"« {n.texte} » : {raison} (…{_extrait(phrase, n)}…)")
                continue

            candidats = [refs[c] for c in cles if c in refs]
            calcules = [i for i in candidats if i.statut == "calcule"]
            noms = "/".join(sorted(i.cle for i in candidats))
            if not calcules:
                etat = "manquant" if candidats and candidats[0].statut == "manquant" else "en erreur"
                problemes.append(
                    f"« {n.texte} » : chiffre attribué à « {noms} », indicateur {etat} "
                    f"(aucune valeur n'existe) (…{_extrait(phrase, n)}…)")
                continue
            if approx:
                problemes.append(f"« {n.texte} » : approximation d'une valeur de « {noms} » "
                                 f"(seule la valeur exacte fournie est permise) (…{_extrait(phrase, n)}…)")
                continue
            bons = [i for i in calcules
                    if _egal(n.valeurs, [i.valeur, *(i.precedentes if compare else ())])]
            if not bons:
                problemes.append(f"« {n.texte} » : ne correspond pas à la valeur de « {noms} » "
                                 f"(…{_extrait(phrase, n)}…)")
                continue
            # Unité : « 12 EUR » ne peut pas décrire un indicateur en clients.
            def unite_ok(i: IndicateurRef) -> bool:
                if unite is None or i.unite is None:
                    return True
                attendue = _canon_unite(i.unite)
                if unite in _UNITES_KPI or unite in unites_connues:
                    return unite == attendue
                return True
            if not any(unite_ok(i) for i in bons):
                problemes.append(f"« {n.texte} » : unité « {unite} » incompatible avec « {noms} » "
                                 f"(…{_extrait(phrase, n)}…)")
    return problemes
