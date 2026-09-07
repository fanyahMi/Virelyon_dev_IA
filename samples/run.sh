#!/usr/bin/env bash
# Teste ARES contre le service IA en cours d'exécution, avec les données de ce dossier.
#
# Prérequis : le service tourne (ex. `make run` → http://localhost:8080).
# Variables (optionnelles) :
#   AI_URL   (défaut http://localhost:8080)
#   AI_KEY   (défaut demo-secret) — doit correspondre à INTERNAL_API_KEY du .env
set -uo pipefail

AI_URL="${AI_URL:-http://localhost:8080}"
AI_KEY="${AI_KEY:-demo-secret}"
DIR="$(cd "$(dirname "$0")" && pwd)"
H=(-H "X-Internal-Key: ${AI_KEY}" -H "Content-Type: application/json")

pp() { if command -v python3 >/dev/null; then python3 -m json.tool 2>/dev/null || cat; else cat; fi; }
call() { # méthode url [fichier]
  local code
  code=$(curl -s -o /tmp/ares_out.json -w "%{http_code}" "${H[@]}" -X "$1" "$2" ${3:+--data @"$3"})
  echo "  [HTTP $code]"; pp < /tmp/ares_out.json; echo
}

echo "############################################################"
echo "# Service : $AI_URL"
echo "############################################################"

echo; echo "### /health (public) ###"
curl -s "$AI_URL/health" | pp; echo

echo; echo "======================================================"
echo " ✅ MARCHE SANS CLÉ CLAUDE (logique pure / déterministe)"
echo "======================================================"

echo; echo "--- Agent Builder (paramétrage du client) ---"

echo "### /builder/referentiels — vocabulaire des listes déroulantes ###"
call GET "$AI_URL/api/v1/builder/referentiels"

echo "### /builder/icp/valider — ICP conforme (aucun diagnostic) ###"
call POST "$AI_URL/api/v1/builder/icp/valider" "$DIR/icp_valider_ok.json"

echo "### /builder/icp/valider — contradictions (min>max, secteur inclus ET exclu) ###"
call POST "$AI_URL/api/v1/builder/icp/valider" "$DIR/icp_valider_contradiction.json"

echo "### /builder/icp/valider — valeurs hors référentiel + fourchette trop étroite ###"
call POST "$AI_URL/api/v1/builder/icp/valider" "$DIR/icp_valider_hors_referentiel.json"

echo "### /builder/icp/valider — ICP vide (ne filtre rien) ###"
call POST "$AI_URL/api/v1/builder/icp/valider" "$DIR/icp_valider_vide.json"

echo "### /builder/plan-recherche — ICP → requêtes par source ###"
call POST "$AI_URL/api/v1/builder/plan-recherche" "$DIR/plan_recherche.json"

echo "### /sourcing/executer — LinkedIn via Apify (SIMULATION : rien n'est dépensé) ###"
call POST "$AI_URL/api/v1/sourcing/executer" "$DIR/sourcing_linkedin.json"

echo; echo "--- Scoring ARES ---"

echo "### /score — lead DANS l'ICP, complet, 2 signaux (~96 → quasi_parfait) ###"
call POST "$AI_URL/api/v1/ares/score" "$DIR/score_bon.json"

echo "### /score — lead fort, 1 signal (~93 → tres_forte) ###"
call POST "$AI_URL/api/v1/ares/score" "$DIR/score_tres_forte.json"

echo "### /score — fit partiel (role hors cibles) (~72 → correcte) ###"
call POST "$AI_URL/api/v1/ares/score" "$DIR/score_correcte.json"

echo "### /score — lead ancien & incomplet mais dans l'ICP (~28 → faible) ###"
call POST "$AI_URL/api/v1/ares/score" "$DIR/score_faible.json"

echo "### /score — pondérations personnalisées (engagement fort) ###"
call POST "$AI_URL/api/v1/ares/score" "$DIR/score_poids_custom.json"

echo "### /score — lead HORS-ICP (secteur exclu → fit 0, score bas) ###"
call POST "$AI_URL/api/v1/ares/score" "$DIR/score_hors_icp.json"

echo "### /score — lead INCOMPLET (faible complétude) ###"
call POST "$AI_URL/api/v1/ares/score" "$DIR/score_incomplet.json"

echo "### /decide — plafond de relance ATTEINT (3/3 → 'arrêt', déterministe) ###"
call POST "$AI_URL/api/v1/ares/decide" "$DIR/decide_plafond.json"

echo; echo "--- L'équipe d'agents du client ---"

echo "### /builder/config — le client et ses agents (ICP partagé, missions distinctes) ###"
call GET "$AI_URL/api/v1/builder/config/11111111-1111-1111-1111-111111111111"

echo "### /score — MÊME lead, vu par le PROSPECTEUR (fit 0.40, engagement 0.10) ###"
call POST "$AI_URL/api/v1/ares/score" "$DIR/agent_score_prospecteur.json"

echo "### /score — MÊME lead, vu par le RELANCEUR (engagement 0.40) → note différente ###"
call POST "$AI_URL/api/v1/ares/score" "$DIR/agent_score_relanceur.json"

echo "### /classify — le PROSPECTEUR n'a pas ce poste → 403 attendu ###"
call POST "$AI_URL/api/v1/ares/classify" "$DIR/agent_refus_prospecteur_classify.json"

echo "### /sourcing/executer — le RELANCEUR n'a pas ce poste → 403 attendu ###"
call POST "$AI_URL/api/v1/sourcing/executer" "$DIR/agent_refus_relanceur_sourcing.json"

echo "### /generate — agent non activé par le client → 409 attendu ###"
call POST "$AI_URL/api/v1/ares/generate" "$DIR/agent_inactif.json"

echo "### /costs — coûts cumulés du workspace, avec le détail par agent ###"
call GET "$AI_URL/api/v1/costs/11111111-1111-1111-1111-111111111111"

echo; echo "======================================================"
echo " ⚠️  NÉCESSITE ANTHROPIC_API_KEY (sinon 503 propre)"
echo "======================================================"

echo; echo "### /builder/icp/extraire — description en langage normal → ICP structuré ###"
call POST "$AI_URL/api/v1/builder/icp/extraire" "$DIR/icp_extraire.json"

echo "### /builder/icp/extraire — description vague (confiance basse attendue) ###"
call POST "$AI_URL/api/v1/builder/icp/extraire" "$DIR/icp_extraire_vague.json"

echo "### /qualify — lead dans l'ICP (Claude Sonnet) ###"
call POST "$AI_URL/api/v1/ares/qualify" "$DIR/qualify.json"

echo "### /qualify — lead hors-ICP (secteur exclu, 450 salariés, stagiaire) ###"
call POST "$AI_URL/api/v1/ares/qualify" "$DIR/qualify_hors_icp.json"

echo "### /classify — Intéressé (Claude Haiku) ###"
call POST "$AI_URL/api/v1/ares/classify" "$DIR/classify.json"

echo "### /classify — À recontacter plus tard ###"
call POST "$AI_URL/api/v1/ares/classify" "$DIR/classify_plus_tard.json"

echo "### /classify — Pas intéressé ###"
call POST "$AI_URL/api/v1/ares/classify" "$DIR/classify_pas_interesse.json"

echo "### /classify — Demande de retrait ###"
call POST "$AI_URL/api/v1/ares/classify" "$DIR/classify_retrait.json"

echo "### /classify — Question hors-scope ###"
call POST "$AI_URL/api/v1/ares/classify" "$DIR/classify_hors_scope.json"

echo "### /generate — 1er contact J0 (Claude Sonnet) ###"
call POST "$AI_URL/api/v1/ares/generate" "$DIR/generate.json"

echo "### /generate — relance J+7 avec historique ###"
call POST "$AI_URL/api/v1/ares/generate" "$DIR/generate_relance.json"

echo "### /generate — 1er contact signé par le PROSPECTEUR (sa mission, email seul) ###"
call POST "$AI_URL/api/v1/ares/generate" "$DIR/agent_generate_prospecteur.json"

echo "### /generate — relance signée par le RELANCEUR (sa mission, email+linkedin) ###"
call POST "$AI_URL/api/v1/ares/generate" "$DIR/agent_generate_relanceur.json"

echo "### /decide — plafond NON atteint (1/3 → décision via Claude) ###"
call POST "$AI_URL/api/v1/ares/decide" "$DIR/decide_continuer.json"

echo "### /decide — réponse positive → escalade (via Claude) ###"
call POST "$AI_URL/api/v1/ares/decide" "$DIR/decide_escalade.json"
