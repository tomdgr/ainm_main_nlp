Deep research and improve a specific competition task — analyse failures, test the API, fix the playbook, create/update a simulator, and verify improvements.

## Input

The user provides a task ID (e.g., `task_23`). If not provided, ask for it.

## Step 1: Gather all evidence

1. **Find ALL run logs** for this task across revisions:
   ```
   find example_runs/tripletex-agent -path "*/task_XX/*_run.txt" | sort
   ```
   Read the 3 most recent runs THOROUGHLY. For each, extract:
   - Exact prompt text
   - File attachments (PDF, CSV) — read the actual file content
   - API call sequence with status codes and responses
   - Error patterns (422, 500, 403, 400)
   - Score result
   - Classification confidence and playbook injection
   - Duration and token usage
   - Which tools were used (parse_structured_data, aggregate_postings, calculate_accounting)

2. **Read the current playbook** in `src/services/run_history.py` — search CURATED_PLAYBOOKS for the task ID. Also check TASK_KEYWORDS for classifier coverage.

3. **Read the current validator rules** in `src/services/api_validator.py` — check if any rules apply to this task's API patterns.

4. **Check if a simulator exists** in `src/simulator/game_simulator.py` — look for the task ID in ALL_TASKS.

## Step 2: Test the API

Use the Tripletex sandbox to verify assumptions. Load credentials from `.env`:
```python
import httpx, os
from dotenv import load_dotenv
load_dotenv()
base = os.getenv('API_URL')
token = os.getenv('SESSION_TOKEN')
```

Test the specific endpoints the task needs:
- Verify field names that caused errors in run logs
- Test the exact API flow from the playbook
- Discover valid enum values, payment types, etc.
- Check which endpoints are [BETA] (return 403)

Use `src/services/openapi_spec.py` to look up endpoint schemas:
```python
from src.services.openapi_spec import OpenAPISpecSearcher
spec = OpenAPISpecSearcher()
spec.load()
print(spec.get_endpoint_details('/path', 'METHOD'))
```

## Step 3: Root cause analysis

For each error pattern found, determine:
1. **Is it a playbook issue?** — Wrong endpoint, wrong field name, missing step
2. **Is it a validator gap?** — Known bad pattern not caught before API call
3. **Is it a classifier issue?** — Task misclassified, wrong playbook injected
4. **Is it an API limitation?** — Endpoint doesn't work as expected in sandbox
5. **Is it a computation issue?** — LLM doing math/parsing that a tool should handle

Present findings as a table:
| Error | Root Cause | Fix Location | Expected Impact |
|-------|-----------|--------------|-----------------|

## Step 4: Implement fixes

In priority order (highest score impact first):

### 4.1 Fix playbook
**File**: `src/services/run_history.py`
- Update golden_path with correct API flow (verified against live API in Step 2)
- Update key_lessons with CRITICAL warnings for known error patterns
- Add missing keywords to TASK_KEYWORDS if classifier coverage is weak

### 4.2 Add validator rules
**File**: `src/services/api_validator.py`
- Add rules for patterns that consistently cause 4xx errors
- Follow existing rule pattern in `_check_hard_rules()`

### 4.3 Create or update simulator
**File**: `src/simulator/tasks/task_<name>.py`

A simulator task needs:
- `name`, `tier`, `optimal_calls`, `prompts` — class attributes
- `extract_expected(prompt)` — parse prompt for expected values
- `setup(base_url, session_token, expected)` — pre-create entities via `self._api()`
- `get_files(expected)` — return file attachments (PDF/CSV) if needed
- `check(verifier, expected)` — verify results via API, return list of Check objects

Key patterns:
- Use `self._api()` helper for setup calls (handles auth and errors)
- Store entity IDs as instance attributes for use in `check()`
- For PDFs: use `pymupdf` to generate (see `task_receipt_expense.py`)
- For CSVs: use string formatting with semicolon delimiter
- Search vouchers with `sorting="-id"` to get most recent first
- Allow tolerance on amounts (±10% for VAT differences)
- Always check if entities exist before creating (sandbox state accumulates)
- Orders REQUIRE both `orderDate` AND `deliveryDate`

Register in `src/simulator/game_simulator.py`:
- Add import
- Add `"task_XX": MyTask("task_XX")` to ALL_TASKS

### 4.4 Reference files
- Existing simulators: `src/simulator/tasks/` (17+ examples)
- Base class: `src/simulator/tasks/base.py` (BaseTask with `_api()` helper and `get_files()`)
- Models: `src/simulator/models.py` (Check, TaskResult)
- Game simulator: `src/simulator/game_simulator.py` (task registration + file attachment support)

## Step 5: Verify

1. **Run the simulator**:
   ```bash
   python test_local.py task_XX
   ```
   The local HTTPS server must be running (`./run_local.sh`).

2. **Check results**: All checks should pass, 0 API errors, reasonable duration (<120s for tier 2, <200s for tier 3).

3. **Check run logs** at `src/logs/runs/local/task_XX/` for:
   - `[PHASE] Executing with playbook` — playbook was injected
   - No `[VALIDATION]` blocks caused by the playbook's own recommendations
   - No 500 errors from wrong paymentTypeId or missing fields
   - Tools used correctly (parse_structured_data for CSV, calculate_accounting for math)

4. **Run regression test**: `python test_local.py task_1 task_2 task_4` to confirm no regressions.

5. If checks fail, iterate: read the new run log, identify the remaining issue, fix, re-run.

## Step 6: Summary

Present:
1. Root causes found and fixes applied
2. Simulator results (checks passed, score, duration, errors)
3. Expected score improvement on competition
4. Any remaining issues that can't be fixed locally

## Important notes

- The leaderboard tracks BEST score per task — bad runs never lower the score
- Scoring: correctness (0-1) × tier. Efficiency bonus only at perfect correctness
- Max per task: tier 1 = 2.0, tier 2 = 4.0, tier 3 = 6.0
- Each 4xx error reduces efficiency bonus by 15%
- Focus on CORRECTNESS first (much bigger impact than efficiency)
- 5 attempts per task per day — prioritise high-confidence fixes
- The sandbox is shared — entity IDs change between sessions
- Think outside the box: can a custom tool solve what the LLM struggles with?
