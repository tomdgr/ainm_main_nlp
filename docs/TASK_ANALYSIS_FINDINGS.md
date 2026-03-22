# Task Analysis Findings (2026-03-22)

Comprehensive analysis of all 11 struggling tasks based on run log analysis, API testing, and competition feedback.

**Total score: 77.24 / 180 (42.9%)**

---

## Common Issues

### 1. Response truncation strips critical data
`essential_keys` in `agent_service.py` is missing keys needed by specific endpoints. The GET /balanceSheet endpoint returns `account`, `balanceIn`, `balanceChange`, `balanceOut` — all stripped by truncation, breaking task 30's tax calculation. The agent falls back to slow manual aggregation.

**Affected tasks:** 30, potentially 28

### 2. System prompt instructs manual bank account setup
The system prompt tells the agent to manually check/set bank account 1920 before invoicing. But the auto-fix in `tripletex_api` already handles this transparently for `POST /invoice`. The manual PUT wastes a write call, reducing efficiency scores.

**Affected tasks:** 06 (gap 0.7), 17 (gap 0.5), 15, any invoicing task

### 3. Blocked API endpoints may need reconsidering
Rule 13 blocks `POST /incomingInvoice` (always 403 on sandbox). Rule 14 blocks `PUT /supplierInvoice/voucher/{id}/postings` (422 on filled vouchers). These are the only paths to create SupplierInvoice entities. Unblocking them lets the agent try on the competition proxy (which may have different permissions).

**Affected tasks:** 11 (0.0), 20 (0.6)

### 4. Multilingual matching failures
Tools match Norwegian-language category names against model descriptions. Non-Norwegian prompts (French, Portuguese, German, Spanish) don't match, causing wrong category fallbacks.

**Affected tasks:** 13 (travel expense cost categories fall back to "Bredbånd")

### 5. STYRK occupation code mapping broken
PDFs contain 4-digit STYRK-08 codes. Tripletex uses 7-digit STYRK-98 codes. The `code=XXXX` search does substring matching, returning wrong or zero results.

**Affected tasks:** 19, 21

---

## Task-Specific Findings

### Task 06 — Invoice Creation (Tier 1, 1.33/2.0)
**Issue:** Pure efficiency problem. Agent uses 3 writes instead of optimal 2.
- Creates unnecessary POST /product before POST /invoice
- Or uses separate POST /order + PUT /order/:invoice instead of inline POST /invoice
- System prompt causes manual bank account PUT (duplicating auto-fix)

**Fix:** Strengthen playbook to mandate POST /invoice with inline order. Remove bank account guidance from system prompt.

### Task 11 — Supplier Invoice (Tier 2, 0.00/4.0)
**Issue:** Chicken-and-egg API limitation. No way to create a SupplierInvoice entity:
- `POST /incomingInvoice` → 403 (BETA, no permission)
- `PUT /supplierInvoice/voucher/{id}/postings` → 422 ("already have postings")
- `POST /ledger/voucher` requires balanced postings (can't create credit-only)

**Untested approaches:** POST /ledger/voucher/importDocument, PUT /ledger/voucher/{id} with guiRow==0 to clear postings, POST /incomingInvoice with sendTo=null.

### Task 12 — Payroll (Tier 2, 1.00/4.0)
**Issue:** Score stuck at exactly 1.00 (50% correctness) across all 17 attempts, even with flawless execution. Likely **missing a finalize/close step** for the salary transaction.
- Bug in `setup_employee_for_payroll`: sends `employmentType` on Employment instead of EmploymentDetails (422 every time)
- Need to find salary transaction close/finalize endpoint

### Task 13 — Travel Expense (Tier 2, 1.38/4.0)
**Issue:** Score stuck at 1.375 across 15 attempts.
- `create_travel_expense` tool matches cost categories by Norwegian name ("Fly", "Taxi")
- Non-Norwegian descriptions ("billet d'avion", "bilhete de avião") don't match → falls back to "Bredbånd"
- Per diem `count` might mean nights (N-1) not days (N)

### Task 19 — Employee from PDF (Tier 3, 2.59/6.0)
**Issue:** 3 of 22 field checks fail. Score = 19/22 * 3 = 2.59.
1. **`bankAccountNumber` never extracted** — PDF always contains "Bankkonto" field but playbook ignores it
2. **STYRK occupation code wrong** — 4-digit PDF codes don't map to 7-digit Tripletex codes
3. **departmentNumber empty** — auto-fix deployed (Rule 19)

### Task 20 — Supplier Invoice PDF (Tier 3, 0.60/6.0)
**Issue:** Same as task 11 — no SupplierInvoice entity created. The 0.60 score means only the supplier existence check passes (1 of ~5 checks).

### Task 21 — Offer Letter PDF (Tier 3, 2.57/6.0)
**Issue:** 3 of 7 checks fail.
1. departmentNumber — fix deployed (Rule 19)
2. email — fix deployed (Rule 16, auto-generates email)
3. Occupation code accuracy — same STYRK issue as task 19

### Task 23 — Bank Reconciliation (Tier 3, 0.60/6.0)
**Issue:** Agent completely ignores the bank reconciliation workflow. Check 1 (worth 8 of 10 points) likely verifies a reconciliation object exists.
- Agent registers payments directly (passes Check 2 = 2pts)
- Never uses: `POST /bank/statement/import`, `POST /bank/reconciliation`, `POST /bank/reconciliation/match`
- These endpoints exist in the API spec and are likely what the competition expects

### Task 28 — Expense Analysis (Tier 3, 1.50/6.0)
**Issue:** 3 of 5 checks fail. Stuck at 1.50 across all 11 attempts.
1. Wrong 3rd expense account — includes salary account 5000 instead of operating expense (6000-7999 range)
2. Parallel POST /project/projectActivity causes 409 race conditions
3. Activities created without `activityType` on first attempt (422 errors)

### Task 29 — Project Lifecycle (Tier 3, 1.64/6.0)
**Issue:** Deployed fixes (participant + vatType removal) had NO EFFECT in revision 00073. Real issues:
1. **Missing project hourly rates** — all timesheet entries show hourlyRate:0.0
2. **Supplier cost should be project order line** (POST /project/orderline), not ledger voucher
3. Invoice may need to be built from project data, not generic inline order

### Task 30 — Year-End Closing (Tier 3, 1.80/6.0)
**Issue:** Checks 4 and 5 fail consistently.
- Check 4 (prepaid reversal): Hardcoded expense account 6300 — should dynamically look up the paired account from existing postings on 1700
- Check 5 (tax provision): balanceSheet data stripped by response truncation (missing essential_keys), forcing inaccurate fallback calculation
