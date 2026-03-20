# Tripletex AI Accounting Agent

AI agent for the NM i AI 2026 Tripletex challenge. Receives natural-language accounting tasks (in 7 languages) and executes them via the Tripletex REST API.

## Architecture

```
POST /solve → FastAPI (Bearer auth) → PydanticAI Agent (Claude Opus 4.6 via Vertex AI)
                                            ├── tripletex_api tool    → Tripletex REST API (via proxy)
                                            ├── search_api_spec tool  → OpenAPI spec keyword search
                                            └── get_endpoint_detail   → Full endpoint schema lookup
```

## Project Structure

```
src/
  main.py                      # FastAPI app (/solve, /health) + auth
  models.py                    # Pydantic request/response models
  services/
    agent_service.py           # PydanticAI agent with tools
    tripletex_client.py        # Async HTTP client for Tripletex API
    openapi_spec.py            # OpenAPI spec loader and search
  prompts/
    system_prompt.py           # Curated system prompt with endpoint reference
  utils/
    logging.py                 # Structured logging + per-run file logs (local/GCS)
Dockerfile                     # Python 3.13 + uv, runs on port 8080
deploy.sh                      # One-command Cloud Run deployment
notebooks/
  test_agent.ipynb             # Manual testing against sandbox
```

## Quick Start

### 1. Install dependencies

```bash
uv sync
```

### 2. Configure `.env`

```bash
cp .env.example .env
# Edit .env with your values
```

Required variables:
```
GOOGLE_CLOUD_PROJECT=ainm26osl-708
GOOGLE_CLOUD_LOCATION=global
```

### 3. Authenticate with GCP

```bash
gcloud auth application-default login
```

### 4. Run locally

```bash
uv run uvicorn src.main:app --port 8000
```

### 5. Test with curl

```bash
curl -X POST http://localhost:8000/solve \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Opprett en ansatt med navn Ola Nordmann, ola@example.org.",
    "files": [],
    "tripletex_credentials": {
      "base_url": "https://kkpqfuj-amager.tripletex.dev/v2",
      "session_token": "YOUR_TOKEN"
    }
  }'
```

### 6. Expose locally for testing (optional)

```bash
npx cloudflared tunnel --url http://localhost:8000
```

## Deployment

### Prerequisites

```bash
# Authenticate with GCP
gcloud auth login
gcloud config set project ainm26osl-708

# Ensure Cloud Run API is enabled
gcloud services enable run.googleapis.com
```

### Deploy

The deploy script reads from `.env` and passes all necessary env vars to Cloud Run:

```bash
./deploy.sh
```

This will:
1. Build the Docker image from source
2. Deploy to Cloud Run in `europe-north1` with 1Gi memory, 300s timeout
3. Set all env vars (GCP project, Logfire, logging, auth key)
4. Print the service URL

### Auth Setup

The `/solve` endpoint is protected with a Bearer token. Set `AGENT_API_KEY` in your `.env`:

```
AGENT_API_KEY=your-secret-key-here
```

When submitting your endpoint URL at [app.ainm.no](https://app.ainm.no/submit/tripletex), enter the same API key. The competition validator will send it as `Authorization: Bearer <your-api-key>` with each request.

Without `AGENT_API_KEY` set, the endpoint is open (useful for local development).

### Update deployment

After code changes, just re-run:

```bash
./deploy.sh
```

### View logs

```bash
gcloud run services logs read tripletex-agent --region europe-north1 --limit 50
```

Tail the logs
```bash
gcloud run services logs tail tripletex-agent  --project ainm26osl-708
gcloud run services logs tail tripletex-agent --region europe-north1 --project ainm26osl-708
gcloud beta run services logs tail tripletex-agent --region europe-north1 --project ainm26osl-708


```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GOOGLE_CLOUD_PROJECT` | GCP project ID | `ainm26osl-708` |
| `GOOGLE_CLOUD_LOCATION` | Vertex AI region | `global` |
| `AGENT_API_KEY` | Bearer token for `/solve` auth (set same key on app.ainm.no) | *(none, open)* |
| `LOGFIRE_API_KEY` | Logfire API key for PydanticAI tracing | - |
| `LOG_FORMAT` | `json` for Cloud Run, `text` for local | `text` |
| `LOG_LEVEL` | Logging level | `INFO` |
| `LOG_STORAGE` | `local` for disk, `gcs` for Cloud Storage | `local` |
| `LOG_BUCKET` | GCS bucket name (required if `LOG_STORAGE=gcs`) | - |
| `LOG_HOST` | Folder prefix for run logs (auto-detects on Cloud Run) | `local` |

## How It Works

1. The competition validator sends a POST to `/solve` with a Bearer token, task prompt, optional file attachments, and Tripletex API credentials.
2. The PydanticAI agent (Claude Opus 4.6) interprets the prompt and plans the API call sequence.
3. The agent uses the `tripletex_api` tool to make REST calls to the Tripletex API via the provided proxy.
4. If the agent encounters an unfamiliar endpoint, it can search the full OpenAPI spec (109K lines, 800 endpoints) via the `search_api_spec` tool.
5. The endpoint returns `{"status": "completed"}` and the validator checks the results field-by-field.
6. Each run is logged to a timestamped file (locally or in GCS) with full tool call traces.

## Scoring

- **Correctness**: Field-by-field checks normalized to 0-1
- **Tier multiplier**: Tier 1 (x1), Tier 2 (x2), Tier 3 (x3)
- **Efficiency bonus**: Up to 2x for perfect scores with minimal API calls and zero 4xx errors
- Max per task: 6.0 (perfect Tier 3 + best efficiency)
