# Changelog

Score progression and changes per Cloud Run revision. Only the latest revision's changes are uncommitted — older entries reflect what was deployed.

## Revision 00020 (next deploy — uncommitted)

**Changes:**
- **Dynamic lessons from previous runs** (`src/services/run_history.py`) — replaces hardcoded "Lessons Learned" in the system prompt. Classifies incoming prompts to one of 18 known task types using multilingual keyword scoring (100% accuracy on test set), then injects a task-specific playbook (optimal API flow + pitfalls) into the user message. Playbooks are ~200-300 tokens each. Includes curated playbooks for all 18 task types + error patterns extracted from 43 parsed run logs.
- **Pre-validation layer** (`src/services/api_validator.py`) — validates API calls against the OpenAPI spec BEFORE making HTTP requests. Catches unknown fields, [BETA] endpoints, wrong enum values, and auto-strips read-only fields. Prevents wasted API calls and 4xx error penalties.
- **Richer endpoint details** (`src/services/openapi_spec.py`) — `get_endpoint_detail` now shows enum values inline (e.g., `userType: string (STANDARD | EXTENDED | NO_ACCESS)`) and expands `$ref` schemas one level to show writable fields.
- **Score tracking in run logs** — each run log now includes a `[SCORE]` line showing previous→new score and whether it improved, e.g., `task_11: 0.00 → 2.80 ✓ IMPROVED (+2.80)`.
- **Slimmed system prompt** — replaced ~800 tokens of task-specific hardcoded lessons with ~200 tokens of general rules. Task-specific knowledge now comes from dynamic playbook injection.
- **Local test script** (`test_local.py`) — run `python test_local.py` to smoke-test changes before deploying.

**Expected impact:**
- All tasks: dynamic playbooks guide the agent with optimal API flows, reducing trial-and-error
- task_11 (supplier invoices): 0.0 → should score >0 with correct voucherType approach
- General: pre-validator prevents wasted API calls; playbooks reduce unnecessary endpoint discovery calls
- New task types: agent falls back to discovery mode (no regression vs current)

**Local test result:** task_1 6/6, task_2 8/8, task_4 5/5 — all perfect, 0 errors, playbooks injected.

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
