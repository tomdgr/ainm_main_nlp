#!/bin/bash
set -e

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-ainm26osl-708}"
REGION="europe-north1"
SERVICE_NAME="tripletex-agent"

# Load .env if present (for local deploys)
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Required env vars for Cloud Run
ENV_VARS="GOOGLE_CLOUD_PROJECT=$PROJECT_ID"
ENV_VARS="$ENV_VARS,GOOGLE_CLOUD_LOCATION=${GOOGLE_CLOUD_LOCATION:-global}"
ENV_VARS="$ENV_VARS,LOG_FORMAT=text"
ENV_VARS="$ENV_VARS,LOG_STORAGE=gcs"

# Optional env vars (only add if set)
[ -n "$LOG_BUCKET" ]     && ENV_VARS="$ENV_VARS,LOG_BUCKET=$LOG_BUCKET"
[ -n "$LOGFIRE_TOKEN" ]   && ENV_VARS="$ENV_VARS,LOGFIRE_TOKEN=$LOGFIRE_TOKEN"
[ -n "$AGENT_API_KEY" ]   && ENV_VARS="$ENV_VARS,AGENT_API_KEY=$AGENT_API_KEY"
[ -n "$TEAM_ID" ]         && ENV_VARS="$ENV_VARS,TEAM_ID=$TEAM_ID"
[ -n "$ACCESS_TOKEN" ]    && ENV_VARS="$ENV_VARS,ACCESS_TOKEN=$ACCESS_TOKEN"

echo "Deploying $SERVICE_NAME to Cloud Run..."
echo "  Project: $PROJECT_ID"
echo "  Region:  $REGION"
echo "  Auth:    $([ -n "$AGENT_API_KEY" ] && echo "Bearer token enabled" || echo "NO AUTH — set AGENT_API_KEY to protect")"

gcloud run deploy "$SERVICE_NAME" \
  --source . \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --allow-unauthenticated \
  --memory 1Gi \
  --timeout 300 \
  --min-instances 1 \
  --set-env-vars "$ENV_VARS"

echo ""
echo "Deployment complete. Service URL:"
gcloud run services describe "$SERVICE_NAME" --project "$PROJECT_ID" --region "$REGION" --format="value(status.url)"
echo ""
echo "Submit this URL at https://app.ainm.no/submit/tripletex"
[ -n "$AGENT_API_KEY" ] && echo "Set the API key to: $AGENT_API_KEY"
