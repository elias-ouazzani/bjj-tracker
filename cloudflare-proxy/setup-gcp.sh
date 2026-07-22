#!/usr/bin/env bash
# =============================================================================
# WIF setup: let a Cloudflare Worker call the PRIVATE Cloud Run service without
# a service-account key (the org blocks keys AND public/allUsers access).
#
# Run in Cloud Shell, from the folder that contains jwks.json (run gen-keys.mjs
# first). Ordering matters: STEP 4 is the fail-fast gate. If it errors with
#   constraints/iam.managed.allowedPolicyMembers
# then this whole approach is blocked by the org — STOP and migrate to a
# personal GCP project instead. Everything before step 4 is cheap/reversible.
# =============================================================================
set -euo pipefail

# --- Config (edit only if something differs from your project) ---
PROJECT_ID="atheal-internship-elias"
PROJECT_NUMBER="264025165631"
SA_EMAIL="bjj-tracker-runtime@atheal-internship-elias.iam.gserviceaccount.com"
REGION="europe-west1"
SERVICE="bjj-tracker"

POOL_ID="cf-proxy-pool"
PROVIDER_ID="cf-proxy-provider"
ISSUER="https://strain-proxy.cf"   # arbitrary string; MUST match Worker ISSUER
AUDIENCE="strain-cf-proxy"         # arbitrary string; MUST match Worker AUDIENCE
SUBJECT="strain-worker"            # arbitrary string; MUST match Worker SUBJECT

gcloud config set project "$PROJECT_ID"

echo "== Step 1: enable required APIs =="
gcloud services enable \
  sts.googleapis.com \
  iamcredentials.googleapis.com \
  iam.googleapis.com \
  run.googleapis.com

echo "== Step 2: create the workload identity pool =="
gcloud iam workload-identity-pools create "$POOL_ID" \
  --location="global" \
  --display-name="Cloudflare proxy pool" \
  || echo "(pool may already exist — continuing)"

echo "== Step 3: create the OIDC provider (inline JWKS — no hosting needed) =="
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
  --location="global" \
  --workload-identity-pool="$POOL_ID" \
  --issuer-uri="$ISSUER" \
  --allowed-audiences="$AUDIENCE" \
  --attribute-mapping="google.subject=assertion.sub" \
  --jwk-json-path="jwks.json" \
  || echo "(provider may already exist — continuing)"

echo ""
echo "=============================================================="
echo "== Step 4 (FAIL-FAST GATE): federated subject -> impersonate SA"
echo "== If this errors with constraints/iam.managed.allowedPolicyMembers,"
echo "== STOP. The org blocks this too — migrate to a personal project."
echo "=============================================================="
MEMBER="principal://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/subject/${SUBJECT}"
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --member="$MEMBER"

echo "== Step 5: keep Cloud Run PRIVATE, grant the runtime SA invoker on it =="
gcloud run services add-iam-policy-binding "$SERVICE" \
  --region="$REGION" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.invoker"

echo ""
echo "== Done. Your PRIVATE Cloud Run URL (put this in wrangler.toml CLOUD_RUN_URL): =="
gcloud run services describe "$SERVICE" --region="$REGION" --format="value(status.url)"
