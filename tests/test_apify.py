"""Connecteur Apify / LinkedIn.

Les charges de test reprennent la **forme réelle** renvoyée par l'acteur
`harvestapi/linkedin-profile-search`, relevée lors d'une exécution de
vérification — y compris ses deux pièges : `currentPosition` vide alors que la
personne a un poste, et `emails` en liste d'objets plutôt qu'en chaîne.
"""
import asyncio

from app.schemas.ares import ICP
from app.schemas.builder import BlocRecherche
from app.schemas.sourcing import ExecuterPlanRequest
from app.sourcing.apify import (
    CHARGE_MINIMALE,
    ApifyLinkedIn,
    _email,
    _poste,
    _vers_lead,
)
from app.sourcing.executeur import executer_plan

WID = "11111111-1111-1111-1111-111111111111"

# Profil au poste absent de `currentPosition` — le cas majoritaire en production.
PROFIL_SANS_CURRENT = {
    "firstName": "Edouard", "lastName": "Epaud",
    "headline": "Co-Founder & CEO @TAMTAM",
    "linkedinUrl": "https://www.linkedin.com/in/edouardepaud",
    "currentPosition": [],
    "experience": [{"position": "Co-Founder & CEO", "companyName": "TAMTAM"}],
    "emails": [{"email": "edouard.epaud@tamtam.ai", "status": "valid",
                "qualityScore": 90}],
    "companyWebsites": [{"url": "https://www.tamtam.ai/?utm_source=linkedin",
                         "domain": "tamtam.ai"}],
    "location": {"parsed": {"city": "Paris", "countryCode": "FR"}},
}

PROFIL_SANS_ENTREPRISE = {
    "firstName": "Sans", "lastName": "Emploi",
    "currentPosition": [], "experience": [],
}

BLOC = BlocRecherche(
    source="linkedin",
    type="filtres",
    filtres={
        "requetes_par_secteur": {"marketing": "marketing"},
        "currentJobTitles": ["Founder"],
        "companyHeadcount": ["C"],
        "seniorityLevelIds": ["320"],
        "locations": ["France"],
    },
)


# ----- Correspondance profil → lead ------------------------------------------
def test_le_poste_est_lu_dans_experience_quand_current_position_est_vide():
    """2 profils sur 3 en production : ce n'est pas un cas défensif."""
    assert _poste(PROFIL_SANS_CURRENT)["companyName"] == "TAMTAM"


def test_le_lead_porte_l_entreprise_pas_la_personne():
    lead = _vers_lead(PROFIL_SANS_CURRENT, "marketing")
    assert lead.nom == "TAMTAM"                      # le prospect, c'est la société
    assert lead.contact["nom"] == "Edouard Epaud"    # la personne est le contact
    assert lead.contact["email"] == "edouard.epaud@tamtam.ai"
    assert lead.contact["linkedin"].endswith("/edouardepaud")


def test_le_role_est_canonise_pour_que_le_scoring_le_compare():
    """« Co-Founder & CEO » doit devenir `fondateur`, sinon le fit tombe à 0."""
    assert _vers_lead(PROFIL_SANS_CURRENT, "marketing").role_contact == "fondateur"


def test_le_site_de_l_entreprise_permet_l_enrichissement_hunter():
    from app.sourcing.base import domaine_de

    assert domaine_de(_vers_lead(PROFIL_SANS_CURRENT, "marketing")) == "tamtam.ai"


def test_le_secteur_n_est_jamais_attribue():
    """La recherche LinkedIn est floue : affirmer le secteur empoisonnerait le fit."""
    lead = _vers_lead(PROFIL_SANS_CURRENT, "marketing")
    assert lead.secteur is None
    assert lead.donnees_brutes["secteur_recherche"] == "marketing"


def test_l_effectif_n_est_jamais_invente():
    """On filtre dessus, l'acteur ne le rend pas — comme Hunter Discover."""
    assert _vers_lead(PROFIL_SANS_CURRENT, "marketing").taille_effectif is None


def test_un_profil_sans_entreprise_est_ecarte():
    """Rien à vendre à personne : autant ne pas payer sa qualification."""
    assert _vers_lead(PROFIL_SANS_ENTREPRISE, "marketing") is None


# ----- Choix de l'email ------------------------------------------------------
def test_une_adresse_valide_prime_sur_une_adresse_risquee():
    profil = {"emails": [
        {"email": "risque@x.co", "status": "risky", "qualityScore": 99},
        {"email": "sur@x.co", "status": "valid", "qualityScore": 40},
    ]}
    assert _email(profil) == ("sur@x.co", "valid")


def test_une_adresse_risquee_est_gardee_mais_etiquetee():
    """Un domaine attrape-tout reste une piste — ARES doit juste le savoir."""
    profil = {"emails": [{"email": "peut-etre@x.co", "status": "risky",
                          "qualityScore": 75}]}
    lead = _vers_lead({**PROFIL_SANS_CURRENT, **profil}, "marketing")
    assert lead.contact["email"] == "peut-etre@x.co"
    assert lead.donnees_brutes["statut_email"] == "risky"


def test_aucun_email_ne_casse_rien():
    lead = _vers_lead({**PROFIL_SANS_CURRENT, "emails": []}, "marketing")
    assert "email" not in lead.contact


# ----- Plafond de dépense ----------------------------------------------------
def test_le_plafond_ne_descend_jamais_sous_le_plancher_apify():
    """Apify rejette (400) toute exécution plafonnée sous 0,10 $."""
    assert ApifyLinkedIn("cle").plafond_depense(1) >= CHARGE_MINIMALE


def test_le_plafond_reserve_une_page_de_recherche():
    """Sans page de réserve, l'acteur refuse de démarrer et rend 0 profil.

    Constaté en production : 0,16 $ (une page + 3 profils, marge comprise) fait
    répondre « max charge reached » après avoir facturé la recherche ; 0,25 $ sur
    la requête identique rend bien les 3 profils. Le plafond doit donc couvrir
    DEUX pages, pas une.
    """
    from app.sourcing.apify import PRIX_PAGE_RECHERCHE

    plafond = ApifyLinkedIn("cle").plafond_depense(3)
    assert plafond >= 2 * PRIX_PAGE_RECHERCHE
    assert plafond > 0.25  # le seuil observé comme fonctionnel


def test_le_plafond_suit_le_volume_demande():
    connecteur = ApifyLinkedIn("cle")
    assert connecteur.plafond_depense(200) > connecteur.plafond_depense(10)


def test_la_recherche_d_email_coute_plus_cher():
    avec = ApifyLinkedIn("cle", chercher_emails=True).plafond_depense(100)
    sans = ApifyLinkedIn("cle", chercher_emails=False).plafond_depense(100)
    assert avec > sans


# ----- Construction de l'appel ----------------------------------------------
def test_l_apercu_masque_le_jeton():
    """Un jeton Apify vaut de l'argent réel : il ne doit jamais fuiter en dry_run."""
    requete = ApifyLinkedIn("jeton-tres-secret").apercu(BLOC, 25)[0]
    assert "jeton-tres-secret" not in str(requete.model_dump())
    assert requete.entetes["Authorization"] == "Bearer ***"


def test_le_jeton_voyage_en_entete_pas_dans_l_url():
    requete = ApifyLinkedIn("cle").apercu(BLOC, 25)[0]
    assert "Authorization" in requete.entetes
    assert not any("token" in c.lower() for c in requete.params)


def test_les_filtres_du_plan_sont_recopies_tels_quels():
    """Les clés portent déjà les noms de l'acteur : aucune traduction à faire."""
    corps = ApifyLinkedIn("cle").apercu(BLOC, 25)[0].corps
    assert corps["searchQuery"] == "marketing"
    assert corps["currentJobTitles"] == ["Founder"]
    assert corps["companyHeadcount"] == ["C"]
    assert corps["seniorityLevelIds"] == ["320"]
    assert corps["profileScraperMode"] == "Full + email search"


def test_une_execution_par_secteur():
    """L'acteur ne prend qu'un searchQuery : deux secteurs, deux exécutions."""
    bloc = BlocRecherche(
        source="linkedin", type="filtres",
        filtres={"requetes_par_secteur": {"marketing": "marketing",
                                          "conseil": "conseil"}},
    )
    requetes = ApifyLinkedIn("cle").apercu(bloc, 50)
    assert [r.corps["searchQuery"] for r in requetes] == ["marketing", "conseil"]
    assert all(r.corps["maxItems"] == 25 for r in requetes)  # plafond réparti


# ----- Exécution -------------------------------------------------------------
def test_une_erreur_applicative_apify_ne_fait_pas_planter(faux_http):
    """Apify rend un objet d'erreur là où un succès rend une liste."""
    faux_http(charge={"error": {"type": "max-total-charge-usd-below-minimum"}},
              source="linkedin")
    leads = asyncio.run(ApifyLinkedIn("cle").executer(BLOC, 25))
    assert leads == []


def test_les_doublons_entre_secteurs_sont_ecartes(faux_http):
    faux_http(charge=[PROFIL_SANS_CURRENT, PROFIL_SANS_CURRENT], source="linkedin")
    leads = asyncio.run(ApifyLinkedIn("cle").executer(BLOC, 25))
    assert len(leads) == 1


# ----- Intégration dans le plan ---------------------------------------------
def _plan(**kw):
    params = dict(
        workspace_id=WID,
        icp=ICP(secteurs_inclus=["marketing"], taille_min=5, taille_max=30,
                roles_cibles=["fondateur"]),
        sources=["linkedin"], zone="France", dry_run=True,
    )
    params.update(kw)
    return asyncio.run(executer_plan(ExecuterPlanRequest(**params)))


def test_linkedin_n_est_plus_une_source_non_implementee():
    assert next(r for r in _plan().par_source if r.source == "linkedin").statut == "simule"


def test_la_reserve_juridique_est_remontee_au_client():
    """Le risque doit rester visible dans la réponse, pas dans un commentaire."""
    messages = " ".join(d.message for d in _plan().diagnostics)
    assert "conditions d'utilisation" in messages
    assert "RGPD" in messages


def test_l_imprecision_du_secteur_est_annoncee():
    messages = " ".join(d.message for d in _plan().diagnostics)
    assert "floue" in messages


def test_la_tranche_d_effectif_evite_le_piege_du_code_A():
    """`A` vaut « Self-Employed » : un ICP 5-30 ne doit pas le retenir."""
    corps = next(
        r for r in _plan().par_source if r.source == "linkedin"
    ).requetes[0].corps
    assert corps["companyHeadcount"] == ["B", "C"]  # 1-10 et 11-50, pas A


def test_sans_jeton_le_statut_est_explicite():
    res = _plan(dry_run=False)
    linkedin = next(r for r in res.par_source if r.source == "linkedin")
    assert linkedin.statut == "non_configuree"
    assert "APIFY_API_TOKEN" in linkedin.erreur
