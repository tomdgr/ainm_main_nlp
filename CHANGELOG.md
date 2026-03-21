# Changelog

Score progression and changes per Cloud Run revision. Only the latest revision's changes are uncommitted — older entries reflect what was deployed.

## Revisions 00055–00056 — 2026-03-21

**Score:** 75.36 → 77.74 (+2.38)

**Key improvements on revision 00055:**
- task_15: 3.00 → 4.00 (perfect — fixed price project)
- task_21: 0.00 → 2.36 (first score! — supplier invoice from PDF, playbook + PDF fix working)
- task_16: 4.00 → 4.00 (maintained)
- task_24: 4.92 → 3.48 (benchmark recalculation — not a regression)

**Key improvements on revision 00056:**
- task_12: 1.00 → still investigating (payroll prereq chain fix deployed)
- task_29: 1.64 → still investigating (project lifecycle keyword fix)
- task_21: 2.36 → still investigating (continued runs)

**Changes deployed (revisions 00055-00056):**
- **9 playbook rewrites**: task_11 (supplier invoice two-step), task_12 (prescriptive prereq chain), task_13 (added `:deliver` step), task_20/21 (incomingInvoice → voucher+PUT postings), task_23 (paymentTypeId lookup + supplier ref), task_28 (unique activities per project), task_29 (keyword fix + sequential timesheets), task_30 (5-voucher with balanceSheet)
- **Computation tools**: `parse_structured_data`, `aggregate_postings`, `calculate_accounting`
- **Validator rules**: paymentTypeId=0 warning, invoiceDueDate required, project/orderline date required
- **json_body: dict|list|None** — enables JSON array bodies for PUT /supplierInvoice/voucher/{id}/postings
- **task_21 classifier fix** — removed duplicate keywords causing ambiguity penalty
- **task_26 classifier fix** — added month-end closing keywords
- **30/30 simulator tasks** — complete coverage with `-j N` parallel support

**Local simulator results (all 30 tasks, -j 4):**
- Total: 67.38/130.0 (51.8%)
- Perfect scores: task_1, 2, 4, 5, 7, 14, 23, 24, 27, 28 (10 tasks)
- Good scores: task_3, 6, 10, 12, 16, 17, 18, 19, 20, 22, 25, 29 (12 tasks)
- Failed/timeout: task_8, 9, 11, 13, 15, 21, 26, 30 (8 tasks — 4 from concurrent overload)

---

## Revisions 00053–00054 — 2026-03-21

**Score:** 65.26 → 75.36 (+10.10)

**Key improvements:**
- task_22: 0.00 → 4.50 (PDF receipt expense — pymupdf extraction confirmed working)
- task_19: 0.60 → 2.59 (PDF employee contract — text extraction enables field extraction)
- task_30: 0.00 → 1.80 (year-end closing — first score)
- task_23: 0.00 → 0.60 (bank reconciliation — playbook injected, but server 500 errors)
- task_27: 6.00 maintained (perfect tier 3)

**Major changes:**
- **Server-side PDF extraction** — `pymupdf` extracts text before sending to Claude. PDFs also passed as native `BinaryContent` document blocks (no more `<pdf>base64</pdf>` text strings). Eliminated FlateDecode thinking loops that exhausted tokens.
- **Computation tools** — 3 new tools offload math/aggregation from LLM thinking:
  - `parse_structured_data`: CSV/TSV/SSV parsing into structured JSON
  - `aggregate_postings`: GROUP BY account + SUM amounts from ledger postings
  - `calculate_accounting`: VAT (gross↔net), depreciation, posting balance validation
- **Accent-insensitive classifier** — `unicodedata.normalize("NFKD")` strips diacritical marks. Portuguese `"salário"` now matches keyword `"salario"`.
- **New playbooks**: task_20 (supplier invoice from PDF), task_21 (alias → task_20), task_23 (bank reconciliation from CSV)
- **Playbook aliases** — `_build_playbooks()` resolves string values as aliases (e.g., task_21 → task_20)
- **task_26 classifier strengthened** — added month-end closing keywords to prevent misclassification as task_12

**Performance impact of computation tools:**
- task_24: 349s → 70.7s (-80%), 450K → 296K tokens (-34%), 4 → 0 errors
- task_28: `aggregate_postings` used for expense analysis

**Simulator expanded** to 19 tasks: added task_3 (product), task_22 (PDF receipt), task_27 (PDF receipt variant)

---

## Revisions 00048–00052 — 2026-03-21

**Score:** 57.75 → 65.26 (+7.52)

**Key improvements:**
- task_16: 2.67 → 4.00 (perfect — timesheet + project invoice)
- task_27: 0.60 → 6.00 (perfect tier 3 — receipt/expense)
- task_26: 3.21 → 4.00 (currency exchange)

**Architecture changes:**
- **Removed planner entirely** — single-phase execution. Eliminated 10-100s planner overhead.
- **Removed dynamic effort routing** — `effort=medium` for all tasks.

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
