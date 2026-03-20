# Tripletex AI Accounting Agent

AI agent for the NM i AI 2026 Tripletex challenge. Receives natural-language accounting tasks (in 7 languages) and executes them via the Tripletex REST API.

## Architecture

```
POST /solve → FastAPI (Bearer auth) → PydanticAI Agent (Claude Opus 4.6 via Vertex AI)
                                            ├── tripletex_api tool   → Tripletex REST API (via proxy)
                                            ├── search_api tool      → Hybrid BM25 + semantic search over OpenAPI spec
                                            └── get_endpoint_detail  → Full endpoint schema lookup
```

## Project Structure

```
src/
  main.py                      # FastAPI app (/solve, /health) + auth
  models.py                    # Pydantic request/response models
  services/
    agent_service.py           # PydanticAI agent with tools
    tripletex_client.py        # Async HTTP client for Tripletex API
    api_search.py              # Hybrid BM25 + semantic endpoint search
    openapi_spec.py            # Legacy keyword search (fallback)
  prompts/
    system_prompt.py           # System prompt with strategy and lessons learned
  utils/
    logging.py                 # Structured logging + per-run file logs (local/GCS)
  simulator/
    game_simulator.py          # Local testing simulator
    models.py                  # Check, TaskResult, SimulatorReport
    tasks/                     # Task definitions with prompts and verification checks
Dockerfile                     # Python 3.13 + uv, runs on port 8080
deploy.sh                      # One-command Cloud Run deployment
notebooks/
  simulator.ipynb              # Run simulator against local agent
  test_tasks.ipynb             # Manual single-task testing
  test_agent.ipynb             # Ad-hoc sandbox exploration
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

### 3. Authenticate with GCP

```bash
gcloud auth application-default login
```

### 4. Run locally

```bash
uv run uvicorn src.main:app --port 8000
```

## Testing & Simulation

### Game Simulator

The simulator runs task prompts against your local agent and verifies results via the Tripletex sandbox API — mimicking the competition's field-by-field scoring.

```bash
# 1. Start the agent
uv run uvicorn src.main:app --port 8000

# 2. Open notebooks/simulator.ipynb and run cells
```

Or from Python:

```python
from src.simulator.game_simulator import GameSimulator

sim = GameSimulator(agent_url="http://localhost:8000")

# Run all tasks
report = await sim.run_all()

# Run a single task
result = await sim.run_task("task_4")

# Run a subset
report = await sim.run_all(task_ids=["task_4", "task_6", "task_8"])
```

**Available tasks:**

| Task ID | Name | Tier | What it tests |
|---------|------|------|---------------|
| `task_1` | Create Departments | 1 | Create 3 departments by name |
| `task_2` | Create Customer | 1 | Customer with name, org number, address, email |
| `task_4` | Create Supplier | 1 | Supplier via /supplier endpoint |
| `task_6` | Create & Send Invoice | 2 | Customer + product + order + invoice + send |
| `task_7` | Register Payment | 2 | Find existing invoice, register full payment |
| `task_8` | Create Project | 2 | Project with customer and project manager |

**Simulator output:**

```
============================================================
SIMULATOR REPORT
============================================================
Task         Checks     Calls      Errors   Score    Max
------------------------------------------------------------
task_1       6/6        3          0        1.67     2.0
task_2       7/7        1          0        2.00     2.0
task_4       5/5        1          0        2.00     2.0
task_6       5/5        5          0        4.00     4.0
task_7       5/5        4          0        4.00     4.0
task_8       7/7        3          0        4.00     4.0
------------------------------------------------------------
TOTAL                                       17.67    18.0
============================================================
```

### Iteration Workflow

1. Make changes to `src/prompts/system_prompt.py` or `src/services/agent_service.py`
2. Restart the server (`uvicorn` auto-reloads on file changes)
3. Run the simulator to check scores
4. If scores improve → deploy: `./deploy.sh`
5. Submit on [app.ainm.no](https://app.ainm.no/submit/tripletex) to get official scores

### Run Logs

Each agent run is logged to timestamped files:
- **Local**: `src/logs/runs/local/{task_id}/YYYYMMDD_HHMMSS_run.txt`
- **Cloud Run**: `gs://{LOG_BUCKET}/runs/{service}/{revision}/YYYYMMDD_HHMMSS_run.txt`

Download cloud logs:
```bash
./download_logs.sh
```

### Manual Testing

For ad-hoc testing without the simulator:
- `notebooks/test_tasks.ipynb` — sends specific prompts per task type
- `notebooks/test_agent.ipynb` — general sandbox exploration

## Deployment

### Deploy

```bash
./deploy.sh
```

Reads `.env`, builds Docker image, deploys to Cloud Run in `europe-north1`.

### Auth Setup

Set `AGENT_API_KEY` in `.env` and use the same key when submitting at app.ainm.no:

```
AGENT_API_KEY=your-secret-key-here
```

### View Cloud Run logs

```bash
gcloud beta run services logs tail tripletex-agent --region europe-north1 --project ainm26osl-708
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GOOGLE_CLOUD_PROJECT` | GCP project ID | `ainm26osl-708` |
| `GOOGLE_CLOUD_LOCATION` | Vertex AI region | `global` |
| `AGENT_API_KEY` | Bearer token for `/solve` auth | *(none, open)* |
| `LOGFIRE_API_KEY` | Logfire tracing token | - |
| `LOG_FORMAT` | `json` for Cloud Run, `text` for local | `text` |
| `LOG_LEVEL` | Logging level | `INFO` |
| `LOG_STORAGE` | `local` for disk, `gcs` for Cloud Storage | `local` |
| `LOG_BUCKET` | GCS bucket name (if `LOG_STORAGE=gcs`) | - |

## Scoring

- **Correctness**: Field-by-field checks normalized to 0-1
- **Tier multiplier**: Tier 1 (x1), Tier 2 (x2), Tier 3 (x3)
- **Efficiency bonus**: Up to 2x for perfect scores with minimal API calls and zero 4xx errors
- Max per task: 6.0 (perfect Tier 3 + best efficiency)
- **Total leaderboard** = sum of best scores across all 30 task types
