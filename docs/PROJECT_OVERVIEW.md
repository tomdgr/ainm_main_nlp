# Project Overview: Tripletex AI Agent

## Competition

NM i AI 2026 — Tripletex challenge. The agent receives accounting tasks in 7 languages (NO-nb, NO-nn, EN, DE, ES, PT, FR) via `POST /solve` and must complete them using the Tripletex REST API. Scored on correctness (field-by-field) and efficiency (fewer write calls, zero errors). 30 tasks across 3 tiers. Max score: 180.

---

## Architecture

```
POST /solve request
    |
    v
Task Classification (keyword scoring, 7 languages)
    |
    v
Playbook Injection (if confidence >= 0.4)
    |
    v
Single-Phase Executor (Claude Opus 4.6 via Vertex AI)
    |-- Tools: tripletex_api, search_api_spec, get_endpoint_detail, ...
    |-- Pre-validation (APIValidator) catches 422s before HTTP
    |-- Auto-fixes (bank account, userType, vatType, postings rows, ...)
    |-- Response truncation (large results → data_store references)
    |
    v
Result + Background task detection (leaderboard + submission feedback)
```

**Key design decisions:**
- Single-phase execution (no separate planner) — faster, fewer tokens
- Playbook in system prompt, not user message — better prompt caching
- GETs are free — only POST/PUT/DELETE count for efficiency scoring
- Leaderboard keeps best score per task — bad runs never lower score

---

## Key Files

| File | Role |
|------|------|
| `src/services/agent_service.py` | Agent: tools, model settings, solve() flow |
| `src/services/run_history.py` | Task classifier + 25 curated playbooks |
| `src/services/api_validator.py` | 19 pre-validation rules (catches 422s before HTTP) |
| `src/services/openapi_spec.py` | Weighted keyword search over Tripletex OpenAPI spec |
| `src/services/pdf_extractor.py` | PyMuPDF text extraction + native PDF passthrough |
| `src/prompts/system_prompt.py` | System prompt with general Tripletex API rules |
| `src/services/leaderboard.py` | Score tracking + submission feedback fetching |
| `src/utils/logging.py` | Per-run logging (GCS in prod, local in dev) |

---

## Tools (14 total)

| Tool | Purpose |
|------|---------|
| `tripletex_api` | Core HTTP tool with pre-validation + auto-fixes |
| `search_api_spec` | Find endpoints by keyword |
| `get_endpoint_detail` | Get full schema for a specific endpoint |
| `parse_structured_data` | Parse CSV/TSV/JSON attachments |
| `aggregate_postings` | Build balanced ledger postings |
| `calculate_accounting` | Depreciation, tax, VAT calculations |
| `analyze_expense_changes` | P&L comparison between date ranges |
| `create_supplier_invoice` | End-to-end supplier invoice workflow |
| `create_travel_expense` | End-to-end travel expense with per diem |
| `setup_employee_for_payroll` | Employee prerequisite chain for payroll |
| `build_voucher_postings` | Construct debit/credit posting rows |
| `think` | Reasoning scratchpad |
| `save_note` / `get_notes` | Working memory within a run |

---

## Auto-Fix Pipeline

Silent fixes applied in `tripletex_api` and `api_validator` before HTTP calls:

- **Bank account 1920**: Auto-set `bankAccountNumber` before invoice creation
- **paymentTypeId**: Auto-fetch valid ID before `/:payment` calls
- **userType + email**: Auto-generate email from name, set `userType: STANDARD`
- **departmentNumber**: Auto-set to "1" on department creation
- **Posting rows**: Renumber from 1 (row 0 is system-reserved)
- **amountGross/Currency**: Sync mismatched values
- **dateFrom==dateTo**: Bump dateTo by 1 day (avoids "From >= To" error)
- **productNumber→number**: Fix fields filter for /product
- **Blocked endpoints**: POST /incomingInvoice (403), PUT /supplierInvoice/voucher postings (422)

---

## Score Status (revision 00069-t8m)

**Total: ~77/180**

### What's Working Well

| Tasks | Score | Notes |
|-------|-------|-------|
| 10, 14, 15, 18, 26 | 4.0/4.0 | Perfect tier-2 scores |
| 27 | 6.0/6.0 | Perfect tier-3 score |
| 25 | 5.25/6.0 | Near-perfect |
| 22 | 4.5/6.0 | PDF receipt → expense voucher |
| 17, 24 | 3.5/4.0 | Dimension voucher, ledger correction |

### Moderate (needs improvement)

| Tasks | Score | Issue |
|-------|-------|-------|
| 01-05, 07, 08 | 1.5-2.0 | Tier-1/2 basics, missing fields |
| 19, 21 | 2.57-2.59 | PDF employee onboarding — missing email/departmentNumber |
| 09, 16 | 3.0/4.0 | Close to max but missing details |

### Struggling

| Task | Score | Max | Root Cause |
|------|-------|-----|------------|
| 29 | 1.64 | 6.0 | Missing project participant + supplier cost VAT splitting |
| 30 | 1.80 | 6.0 | Tax calculation uses wrong data source on competition |
| 28 | 1.50 | 6.0 | Expense analysis tool accuracy |
| 13 | 1.38 | 4.0 | Travel expense field names (15+ gotchas) |
| 12 | 1.00 | 4.0 | Payroll prerequisite chain complexity |
| 06 | 1.33 | 4.0 | Invoice creation missing fields |
| 23 | 0.60 | 6.0 | Bank reconciliation matching logic |
| 20 | 0.60 | 6.0 | Supplier invoice entity not created |
| 11 | 0.00 | 4.0 | PUT /supplierInvoice always returns 422 |

---

## Key Struggles

### 1. Supplier Invoice Entity (task 11, 20)
The Tripletex API has no working endpoint to create a proper SupplierInvoice entity. `POST /incomingInvoice` returns 403, `PUT /supplierInvoice/voucher/{id}/postings` returns 422. We create the voucher correctly but the competition checks for the SupplierInvoice entity.

### 2. Competition vs Sandbox Differences
Competition uses fresh sandboxes per task. Our shared sandbox has accumulated state. Some endpoints behave differently. We cannot inspect competition checks — only get "Check N: passed/failed".

### 3. VAT Treatment Ambiguity
When a task says "cost of 60750 NOK", is that gross (incl. VAT) or net? Using `vatType:1` splits the amount (80% expense + 20% VAT account). Several tasks may need vatType 0 to keep the full amount on the expense account.

### 4. Field Completeness on PDF Tasks
PDFs don't always contain all fields the competition checks (e.g., email, departmentNumber). The agent must infer or generate missing values. Auto-fixes in the validator now handle email and departmentNumber.

### 5. Complex Multi-Step Flows
Tasks 12 (payroll), 13 (travel expense), 29 (project lifecycle) require 5-10 sequential API calls with specific field names and ordering. The LLM gets 2-3 details wrong each time. Workflow tools help but can't cover every variant.

---

## Development Workflow

```bash
# Local development
./run_local.sh                    # Start agent (HTTPS, auto-reload)
python test_local.py task_29      # Test specific task locally
python test_local.py -j 4         # Run all 30 tasks, 4 concurrent

# Deploy
./deploy.sh                       # Deploy to Cloud Run

# Analyze competition results
# 1. Download logs from GCS
gsutil -m cp -r "gs://tripletex-ai-agent-logs/runs/tripletex-agent/<revision>/" example_runs/...
# 2. Check feedback JSON files for per-check pass/fail
# 3. Compare run.txt traces between revisions
```

**Improvement cycle:** Read competition run logs → identify failing checks → test API on sandbox → update playbook/validator/tools → deploy → verify.
