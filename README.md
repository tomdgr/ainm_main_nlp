# Tripletex AI Accounting Agent

AI agent for the NM i AI 2026 Tripletex challenge. Receives natural-language accounting tasks (in 7 languages) and executes them via the Tripletex REST API.

## Architecture

```
POST /solve → FastAPI (Bearer auth)
                  │
                  ▼
           RunHistoryService
           (classify task → inject matching playbook)
                  │
                  ▼
           Single-Phase Executor (Sonnet 4.6, effort=medium, adaptive thinking)
                  ├── tripletex_api tool   → Validator → Auto-fixes → Tripletex REST API
                  ├── search_api_spec      → Weighted search over OpenAPI spec
                  └── get_endpoint_detail  → Full endpoint schema lookup

Key features:
  - Single-phase execution: no separate planner — executor handles discovery + execution
  - Playbook injection via dynamic system prompt for known task types (26+ playbooks)
  - Pre-validation: catches wrong fields, auto-fixes row=0, amountGross mismatch
  - API response truncation: reduces context growth on multi-call tasks
  - PDF/file attachments stored alongside run logs
  - effort=medium for all tasks (Anthropic-recommended for agentic workflows)
```

## Project Structure

```
src/
  main.py                      # FastAPI app (/solve, /health) + auth
  models.py                    # Pydantic request/response models
  services/
    agent_service.py           # Single-phase executor agent (Sonnet 4.6, effort=medium)
    tripletex_client.py        # Async HTTP client for Tripletex API
    api_validator.py           # Pre-validates + auto-fixes API calls (row=0, amountGross, fields)
    run_history.py             # Task classifier (30 types) + curated playbooks (25+ tasks)
    openapi_spec.py            # Weighted search over OpenAPI spec (path/summary/synonym scoring)
    leaderboard.py             # Competition leaderboard polling + task detection
  prompts/
    system_prompt.py           # System prompt with strategy and general rules
  utils/
    logging.py                 # Structured logging + per-run file logs (local/GCS)
  simulator/
    game_simulator.py          # Local testing simulator
    models.py                  # Check, TaskResult, SimulatorReport
    tasks/                     # Task definitions with prompts and verification checks
example_runs/                  # Run logs from competition (used for dynamic lessons)
test_local.py                  # Local smoke test script
Dockerfile                     # Python 3.13 + uv, runs on port 8080
deploy.sh                      # One-command Cloud Run deployment
CHANGELOG.md                   # Score progression per revision
notebooks/
  simulator.ipynb              # Run simulator against local agent
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

The Tripletex API requires HTTPS. Use the local HTTPS script (generates self-signed certs via `mkcert` on first run):

```bash
./run_local.sh
```

This starts uvicorn with HTTPS on `https://localhost:8000` with auto-reload.

Alternatively, without HTTPS (won't work for Tripletex API calls):
```bash
uv run uvicorn src.main:app --port 8000
```

## Testing & Simulation

### Game Simulator

The simulator runs task prompts against your local HTTPS agent and verifies results via the Tripletex sandbox API — mimicking the competition's field-by-field scoring.

```bash
# 1. Start the agent (HTTPS)
./run_local.sh

# 2. Run the test script (in another terminal)
python test_local.py                        # all tasks
python test_local.py task_1 task_2 task_4    # quick tier 1 check
python test_local.py task_6 task_7 task_8    # tier 2 tasks

# Or use the notebook
# notebooks/simulator.ipynb
```

The test script runs the simulator and then analyses the new run logs, showing validation catches and API errors.

**Available tasks (16):**

| Task ID | Name | Tier | What it tests |
|---------|------|------|---------------|
| `task_1` | Create Departments | 1 | Create 3 departments by name |
| `task_2` | Create Customer | 1 | Customer with name, org number, address, email |
| `task_4` | Create Supplier | 1 | Supplier via /supplier endpoint |
| `task_6` | Create & Send Invoice | 2 | Customer + product + order + invoice + send |
| `task_7` | Register Payment | 2 | Find existing invoice, register full payment |
| `task_8` | Create Project | 2 | Project with customer and project manager |
| `task_9` | Post Expense Voucher | 2 | Voucher posting with account, department, VAT |
| `task_10` | Create Employee (Full) | 2 | Employee + employment + salary + occupation code |
| `task_14` | Credit Note | 2 | Credit note on existing invoice |
| `task_15` | Fixed Price Project | 2 | Fixed price project + partial invoice |
| `task_16` | Timesheet Invoice | 2 | Log hours + project invoice |
| `task_17` | Dimension Voucher | 2 | Accounting dimension + values + voucher |
| `task_18` | Reverse Payment | 2 | Reverse bank payment (returned) |
| `task_24` | Ledger Correction | 3 | Find & correct 4 ledger errors |
| `task_25` | Overdue Invoice | 3 | Overdue invoice + reminder fee + partial payment |
| `task_26` | Currency Exchange | 3 | Currency exchange agio/disagio |

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

1. Make changes (prompts, tools, playbooks, etc.)
2. Server auto-reloads on file changes (if started with `./run_local.sh`)
3. Run `python test_local.py task_1 task_2 task_4` to smoke-test
4. Check run logs for `[VALIDATION]` catches and `[LESSONS]` injection
5. If tests pass → deploy: `./deploy.sh`
6. Submit on [app.ainm.no](https://app.ainm.no/submit/tripletex) to get official scores
7. Download logs: `./download_logs.sh` — check `[SCORE]` lines to see which runs improved

### Run Logs

Each agent run is logged to timestamped files with structured tags:

- **Local**: `src/logs/runs/local/{task_id}/no_{attempt}_YYYYMMDD_HHMMSS_run.txt`
- **Cloud Run**: `gs://{LOG_BUCKET}/runs/{service}/{revision}/{task_id}/no_{attempt}_YYYYMMDD_HHMMSS_run.txt`

Key log tags:
| Tag | Description |
|-----|-------------|
| `[PROMPT]` | The task prompt received |
| `[LESSONS]` | Dynamic playbook injected (includes task type + confidence) |
| `[PHASE]` | Execution phase: "Executing with playbook" or "Executing without playbook" |
| `[VALIDATION]` | Pre-validator blocked a bad API call (saved a real call) |
| `[FILE]` | PDF/image attachment stored alongside logs |
| `[API]` | HTTP request with status code and response |
| `[SCORE]` | Score change after run: `task_11: 0.00 → 2.80 ✓ IMPROVED` |
| `[DONE]` | Summary: duration, api_calls, api_errors |

Download cloud logs:
```bash
./download_logs.sh
```

Find score improvements:
```bash
grep "IMPROVED" example_runs/**/**/*_run.txt
```

View Cloud Run console logs:
```bash
gcloud beta run services logs tail tripletex-agent --region europe-north1 --project ainm26osl-708
```

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
- **Efficiency bonus**: Only when correctness = 1.0. `tier + tier × (optimal_calls/actual_calls × max(0, 1 - errors×0.15))`. Only write calls (POST/PUT/DELETE) count; GETs are free.
- Max per task: 6.0 (perfect Tier 3 + best efficiency)
- **Total leaderboard** = sum of best scores across all 30 task types
