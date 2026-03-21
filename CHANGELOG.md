# Changelog

Score progression and changes per Cloud Run revision. Only the latest revision's changes are uncommitted — older entries reflect what was deployed.

## Revisions 00048–00052 — 2026-03-21

**Score:** 57.75 → 65.26 (+7.52)

**Key improvements:**
- task_16: 2.67 → 4.00 (perfect — timesheet + project invoice)
- task_27: 0.60 → 6.00 (perfect tier 3 — receipt/expense)
- task_22: 0.00 → 6.00 (perfect tier 3 — receipt expense, new playbook + keywords)
- task_26: 3.21 → 4.00 (currency exchange)
- task_16: 2.67 → 4.00 (perfect — timesheet + invoice)
- task_09: 3.00 → 3.00 (1 new attempt, no change)

**Architecture changes:**
- **Removed planner entirely** — single-phase execution. Executor handles endpoint discovery + API calls inline. Eliminated 10-100s planner overhead.
- **Removed dynamic effort routing** — `effort=medium` for all tasks. Low effort gave marginal gains on simple tasks but hurt complex ones.
- **Removed effort routing misclassification risk** — task_29 was being routed to `low` effort because it was classified as task_08.

**Confirmed working:**
- Single-phase execution: all runs complete under 300s (0 timeouts on non-PDF tasks)
- Playbook injection: logged as `[PHASE] Executing with playbook (task_XX, conf=X.XX)`
- Response truncation: context growth stays manageable on multi-step tasks
- API search: weighted scoring finds correct endpoints

**Still failing (0 points):**
- task_19, task_20, task_21: PDF extraction tasks — compressed PDF content exhausts thinking tokens
- task_23: Unknown task requirements (supplier creation succeeds but scores 0)
- task_30: Year-end depreciation, complex multi-step

**Biggest remaining opportunities:**
- PDF handling (tasks 19-21): 18.0 potential points. Server-side PDF extraction needed.
- task_11 (supplier invoice): 0/4.0 — agent uses wrong approach despite 8 attempts
- task_13 (travel expense): 1.375/4.0 — unknown scoring gap
- task_29 (project lifecycle): 1.64/6.0 — partially working but too complex for single run

**Simulator expanded** to 16 tasks (was 8): added task_14 through task_18 and task_24 through task_26.

---

## Revisions 00032–00047 — 2026-03-21

**Score:** 39.9 → 52.3 (+12.4, mostly from tier 3 release — new tasks became available)

**Tier 1-2 improvements:**
- task_10: 3.0 → 4.0 (inline invoice + payment)
- task_17: 2.43 → 3.50 (accounting dimensions)

**New tier 3 scores (tasks released during this period):**
- task_25: 5.25/6.0 (overdue invoice + reminder fee)
- task_26: 3.21/6.0 (currency exchange agio/disagio)
- task_29: 1.09/6.0 (year-end adjustments)
- task_28: 1.50/6.0 (expense analysis + projects)

**Architecture changes:**
- **Two-phase Plan→Execute** — Sonnet 4.6 planner (spec tools only) → Sonnet 4.6 executor (all tools). Planner SKIPPED when playbook confidence >= 0.5 (saves 10-100s).
- **Dynamic effort routing** — `low` for simple tasks (task_02-05), `medium` default, `high` for complex/unknown. Uses Anthropic `effort` parameter.
- **API search rewrite** — weighted scoring (path segments 5.0 > summary 2.0 > tags 1.0), method-aware boosting, synonym expansion. Fixed critical failures where "department create" and "project create" returned wrong endpoints.
- **Response truncation** — 200/201 responses trimmed to essential fields (id, version, name, etc.) to reduce context growth.

**Validator auto-fixes:**
- `row=0` in voucher postings → auto-renumbered from 1
- `amountGross != amountGrossCurrency` → auto-synced
- `POST /project` without `projectManager` or `startDate` → blocked with fix instructions
- `GET /ledger/postingByDate` with `fields` param → blocked (not supported)
- `PUT /invoice/:payment` with body params → blocked (must be query params)

**New playbooks added (25+ total):**
- Tier 3: task_19 (employee from PDF), task_22 (receipt expense), task_24 (ledger error correction), task_25 (overdue invoice), task_26 (currency agio/disagio), task_28 (expense analysis + projects), task_29 (monthly closing), task_30 (year-end depreciation + tax)

**Other:**
- PDF/file attachments stored alongside run logs
- `thinking.type=adaptive` (Vertex AI compatible, no budget_tokens)
- Graceful handling of planner tool_calls_limit exceeded (partial plan extraction)
- New simulator tasks: task_9 (voucher expense), task_10 (employee with full details)

---

## Revision 00020 — 2026-03-21

**Score:** 39.6 (+0.5)

**Changes:**
- Dynamic lessons from previous runs (run_history.py) — 18 task types with keyword classifier + curated playbooks
- Pre-validation layer (api_validator.py) — catches unknown fields, BETA endpoints, wrong enum values
- Richer endpoint details with inline enums and expanded $ref schemas
- Score tracking in run logs with [SCORE] tag
- Local test script (test_local.py)

---

## Revision 00019-rnl — 2026-03-20

**Score:** 39.15 (no change)
**Runs:** 3 (task_05, task_07, task_12)

**Changes:** Prompt tuning from revision 00018. Minor system prompt updates.

**Results:** No score improvement. task_12 stayed at 1.0/4.0, task_05 at 1.33/2.0.

---

## Revision 00018-vhz — 2026-03-20

**Score:** 39.15 (−0.05 from benchmark recalculation)
**Runs:** 4 (task_04, task_06, task_15, unclassified)

**Changes:** System prompt updates — supplier invoice lesson, bank account proactive check, employee startDate lesson, invoiceDueDate requirement.

**Results:** task_15 had 12× 403 errors from proxy token expiration (infrastructure issue, not agent). Other tasks ran clean. No score improvement due to no new task types attempted.

---

## Revision 00017-649 — 2026-03-20

**Score:** 39.15 (−0.05 from benchmark recalculation)
**Runs:** 22 across 11 tasks + 2 unclassified

**Changes:** Race condition fix in leaderboard task detection (`_claimed` set + `asyncio.Lock`). Run logs now use end-time timestamps. Attempt numbers tracked in filenames.

**Results:** Logging fixed — no more duplicate/missing files. Score unchanged (benchmark recalculation reduced 17→2.142, total from 39.20→39.15).

---

## Revision 00016-rpz — 2026-03-20

**Score:** 39.20 (no change)
**Runs:** 1 (task_13)

**Changes:** Leaderboard detection improvements, initial/latest score JSON tracking per revision.

**Results:** task_13 attempt scored 1.375 (no improvement over previous best).

---

## Revision 00015-dtp — 2026-03-20

**Score:** 39.20 (+5.95)
**Runs:** 11 across 8 tasks

**Changes:** Leaner system prompt (removed hardcoded body schemas, discovery-first approach). [BETA] endpoint filtering in OpenAPI spec searcher. Background leaderboard task detection. Per-revision log folders.

**Key results:**
- task_14: 0 → 4.0 (+4.0) — perfect score
- task_17: 0 → 2.19 (+2.19)
- task_13: 0 → 1.375 (+1.375)
- task_03: 1.5 → 2.0 (+0.5)

---

## Revision 00014-cpd — 2026-03-20

**Score:** 33.20 (+10.52)
**Runs:** ~15 across tier 2 tasks

**Changes:** Extended thinking enabled (8000 token budget). Prompt caching. Parallel tool calls. Task-specific log subfolders. First tier 2 task attempts.

**Key results:**
- First tier 2 scores: task_09 (2.8), task_10 (3.0), task_15 (3.5), task_16 (3.0), task_18 (4.0)
- task_11: 0.0 (supplier invoice — agent used wrong approach)

---

## Revision 00013-xxm — 2026-03-20

**Score:** 22.68

**Changes:** Initial V1 agent. PydanticAI + Claude Opus 4.6 via Vertex AI. Basic system prompt with hardcoded endpoint rules. Keyword search over OpenAPI spec. Logfire tracing.

**Results:** Solved 7 tier 1 tasks. Baseline established.
