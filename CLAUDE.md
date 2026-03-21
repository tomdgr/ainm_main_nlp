# CLAUDE.md

## What is this?

An AI agent for the NM i AI 2026 Tripletex challenge. The agent receives accounting tasks in 7 languages via POST /solve, then uses the Tripletex REST API to complete them. Scored on correctness (field-by-field) and efficiency (fewer write calls, zero errors).

## How to run

```bash
./run_local.sh                    # Start agent (HTTPS, auto-reload)
python test_local.py -j 4         # Run all 30 simulator tasks, 4 concurrent
python test_local.py task_1       # Run a single task
./deploy.sh                       # Deploy to Cloud Run
```

## Key files

- `src/services/agent_service.py` — The agent: tools, model settings, solve() flow
- `src/services/run_history.py` — Task classifier (TASK_KEYWORDS) + playbooks (CURATED_PLAYBOOKS)
- `src/services/api_validator.py` — Pre-validates API calls before HTTP (catches 422s)
- `src/services/openapi_spec.py` — Weighted search over the Tripletex OpenAPI spec
- `src/services/pdf_extractor.py` — Server-side PDF text extraction (pymupdf)
- `src/prompts/system_prompt.py` — System prompt with general rules
- `src/simulator/tasks/` — 30 simulator task definitions
- `example_runs/` — Run logs from competition (used for playbook development)

## How the agent works

1. Prompt arrives → `RunHistoryService.classify_prompt()` matches to task type via keywords
2. Matching playbook injected into the executor's system prompt
3. Single-phase executor (Opus 4.6) uses tools: `tripletex_api`, `search_api_spec`, `get_endpoint_detail`, `parse_structured_data`, `aggregate_postings`, `calculate_accounting`
4. `APIValidator` catches known bad patterns before HTTP calls
5. Response truncation keeps context manageable

## How to improve a task

Use the `/improve-task` skill or follow this process:

1. Read run logs in `example_runs/` for the task — understand what fails
2. Test the API using the sandbox (credentials in `.env`)
3. Update the playbook in `run_history.py` (CURATED_PLAYBOOKS)
4. Add validator rules in `api_validator.py` if there are recurring 422 patterns
5. Run the simulator: `python test_local.py task_XX`
6. Deploy and check competition scores

## Scoring

- Correctness (0-1) × tier multiplier. Tier 1 (×1, max 2.0), Tier 2 (×2, max 4.0), Tier 3 (×3, max 6.0)
- Efficiency bonus only at perfect correctness: `tier + tier × (optimal/actual × max(0, 1 - errors×0.15))`
- Only write calls (POST/PUT/DELETE) count for efficiency. GETs are free.
- Leaderboard tracks BEST score per task — bad runs never lower it

## Available skills

- `/analyse-revision <id>` — Analyse a Cloud Run revision's scores and run logs
- `/improve-task <task_id>` — Deep research + fix playbook + create simulator + verify
- `/test-local` — Run the game simulator
- `/test-api` — Test Tripletex API endpoints directly

## Important context

- The Tripletex sandbox is shared — state accumulates between test runs
- Competition uses fresh sandboxes per task — don't rely on pre-existing entities
- Some [BETA] endpoints return 403 on the competition proxy
- Run logs are stored in GCS: `gsutil -m cp -r "gs://tripletex-ai-agent-logs/runs/tripletex-agent/<revision>/" example_runs/...`
