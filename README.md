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
           Single-Phase Executor (Opus 4.6, effort=medium, adaptive thinking)
                  ├── tripletex_api tool       → Validator → Auto-fixes → Tripletex REST API
                  ├── search_api_spec          → Weighted search over OpenAPI spec
                  ├── get_endpoint_detail      → Full endpoint schema lookup
                  ├── parse_structured_data    → CSV/TSV parsing into JSON
                  ├── aggregate_postings       → Ledger posting aggregation (GROUP BY account)
                  └── calculate_accounting     → VAT, depreciation, posting validation

Key features:
  - Single-phase execution: no separate planner — executor handles discovery + execution
  - Playbook injection via dynamic system prompt for all 30 task types
  - Server-side PDF extraction (pymupdf) + native BinaryContent passthrough to Claude
  - Computation tools: offload math/aggregation from LLM thinking to deterministic tools
  - Pre-validation: catches wrong fields, auto-fixes row=0, amountGross mismatch
  - Accent-insensitive classifier for multilingual keyword matching
  - API response truncation: reduces context growth on multi-call tasks
  - effort=medium for all tasks (Anthropic-recommended for agentic workflows)
```

## Project Structure

```
src/
  main.py                      # FastAPI app (/solve, /health) + auth
  models.py                    # Pydantic request/response models
  services/
    agent_service.py           # Single-phase executor agent (Opus 4.6) + 6 tools
    tripletex_client.py        # Async HTTP client for Tripletex API
    api_validator.py           # Pre-validates + auto-fixes API calls (10 rules: row=0, amountGross, paymentTypeId, invoiceDueDate, etc.)
    run_history.py             # Task classifier (30 types, accent-insensitive) + curated playbooks (30 tasks)
    openapi_spec.py            # Weighted search over OpenAPI spec (path/summary/synonym scoring)
    pdf_extractor.py           # Server-side PDF text extraction via pymupdf
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
python test_local.py                           # all 30 tasks (sequential)
python test_local.py -j 4                      # all 30 tasks, 4 concurrent
python test_local.py task_1 task_2 task_4       # quick tier 1 check
python test_local.py -j 3 task_6 task_7 task_8  # 3 tasks in parallel
```

The test script runs the simulator and then analyses the new run logs, showing validation catches and API errors. Use `-j N` to run N tasks concurrently (recommended: `-j 4`).

**All 30 tasks have simulators:**

| Task ID | Name | Tier | What it tests |
|---------|------|------|---------------|
| `task_1` | Create Departments | 1 | Create 3 departments by name |
| `task_2` | Create Customer | 1 | Customer with name, org number, address, email |
| `task_3` | Create Product | 1 | Product with number, price excl VAT, VAT type |
| `task_4` | Create Supplier | 1 | Supplier via /supplier endpoint |
| `task_5` | Create Departments | 1 | Same as task_1 (variant) |
| `task_6` | Create & Send Invoice | 2 | Customer + product + order + invoice + send |
| `task_7` | Register Payment | 2 | Find existing invoice, register full payment |
| `task_8` | Create Project | 2 | Project with customer and project manager |
| `task_9` | Post Expense Voucher | 2 | Voucher posting with account, department, VAT |
| `task_10` | Create Employee (Full) | 2 | Employee + employment + salary + occupation code |
| `task_11` | Supplier Invoice | 2 | Register supplier invoice (two-step: voucher + PUT postings) |
| `task_12` | Payroll | 2 | Salary transaction with employment prerequisites |
| `task_13` | Travel Expense | 2 | Per diem + costs + deliver step |
| `task_14` | Credit Note | 2 | Credit note on existing invoice |
| `task_15` | Fixed Price Project | 2 | Fixed price project + partial invoice |
| `task_16` | Timesheet Invoice | 2 | Log hours + project invoice |
| `task_17` | Dimension Voucher | 2 | Accounting dimension + values + voucher |
| `task_18` | Reverse Payment | 2 | Reverse bank payment (returned) |
| `task_19` | Employee from PDF | 3 | PDF contract → extract fields → create employee + employment |
| `task_20` | Supplier Invoice PDF | 3 | PDF invoice → create supplier + register invoice |
| `task_21` | Supplier Invoice PDF | 3 | Same as task_20 (variant) |
| `task_22` | Expense from PDF Receipt | 3 | PDF receipt → extract item → post voucher |
| `task_23` | Bank Reconciliation | 3 | CSV bank statement → match payments + post vouchers |
| `task_24` | Ledger Correction | 3 | Find & correct 4 ledger errors |
| `task_25` | Overdue Invoice | 3 | Overdue invoice + reminder fee + partial payment |
| `task_26` | Currency Exchange | 3 | Currency exchange agio/disagio |
| `task_27` | Expense from PDF Receipt | 3 | Same as task_22 (variant) |
| `task_28` | Expense Analysis | 3 | Analyze ledger deltas + create projects with activities |
| `task_29` | Project Lifecycle | 3 | Project + timesheet + supplier costs + customer invoice |
| `task_30` | Year-End Closing | 3 | Depreciation + prepaid reversal + tax provision |

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
