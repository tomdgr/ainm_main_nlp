Run the local game simulator to smoke-test agent changes before deployment.

## Prerequisites

The local HTTPS server must be running in another terminal:
```
./run_local.sh
```

## Usage

```bash
python test_local.py                           # all 30 tasks (sequential)
python test_local.py -j 4                      # all 30 tasks, 4 concurrent (recommended)
python test_local.py task_1 task_2 task_4       # quick tier 1 (~30s)
python test_local.py -j 3 task_6 task_7 task_8  # 3 tier 2 tasks in parallel
python test_local.py task_22 task_23 task_28     # specific tier 3 tasks
```

Use `-j N` to run N tasks concurrently. Recommended: `-j 4` for full runs (~15 min vs ~45 min sequential).

## All 30 simulator tasks

| Task | Name | Tier | What it tests |
|------|------|------|---------------|
| task_1 | Create Departments | 1 | Create 3 departments by name |
| task_2 | Create Customer | 1 | Customer with address, org number, email |
| task_3 | Create Product | 1 | Product with number, price, VAT type |
| task_4 | Create Supplier | 1 | Supplier with org number, emails |
| task_5 | Create Departments | 1 | Same as task_1 (variant) |
| task_6 | Invoice | 2 | Customer + product + order + invoice + send |
| task_7 | Payment | 2 | Register payment on existing invoice |
| task_8 | Project | 2 | Project with customer and project manager |
| task_9 | Voucher | 2 | Expense voucher posting with VAT |
| task_10 | Employee | 2 | Employee + employment + salary + occupation code |
| task_11 | Supplier Invoice | 2 | Two-step: voucher + PUT supplierInvoice postings |
| task_12 | Payroll | 2 | Salary with employment prerequisite chain |
| task_13 | Travel Expense | 2 | Per diem + costs + deliver step |
| task_14 | Credit Note | 2 | Credit note on existing invoice |
| task_15 | Fixed Price Project | 2 | Fixed price project + partial invoice |
| task_16 | Timesheet Invoice | 2 | Log hours + project invoice |
| task_17 | Dimension Voucher | 2 | Accounting dimension + voucher |
| task_18 | Reverse Payment | 2 | Reverse bank payment (returned) |
| task_19 | Employee from PDF | 3 | PDF contract → employee + employment |
| task_20 | Supplier Invoice PDF | 3 | PDF invoice → supplier + invoice |
| task_21 | Supplier Invoice PDF | 3 | Same as task_20 (variant) |
| task_22 | Receipt PDF | 3 | PDF receipt → expense voucher |
| task_23 | Bank Reconciliation | 3 | CSV → match payments + vouchers |
| task_24 | Ledger Correction | 3 | Find & correct 4 ledger errors |
| task_25 | Overdue Invoice | 3 | Overdue + reminder + partial payment |
| task_26 | Currency Exchange | 3 | Agio/disagio vouchers |
| task_27 | Receipt PDF | 3 | Same as task_22 (variant) |
| task_28 | Expense Analysis | 3 | Ledger analysis + projects + activities |
| task_29 | Project Lifecycle | 3 | Project + timesheet + supplier + invoice |
| task_30 | Year-End Closing | 3 | Depreciation + prepaid + tax provision |

## What to look for

| Signal | Meaning |
|--------|---------|
| All checks pass, 0 errors | Safe to deploy |
| Checks pass but API errors | Works but efficiency dropped |
| Validation catches + checks pass | Validator saving API calls (good) |
| Failed checks | Regression — investigate before deploying |
| Task errors (timeout) | Agent took too long — may work at lower parallelism |

## Important caveats

- The simulator uses a **shared sandbox** — state accumulates. Some tasks may behave differently on a fresh account (like competition provides).
- With `-j 4`, some tasks may timeout if the agent server is overloaded. Run them individually to verify.
- This is a **pre-validation step**, not ground truth. Always verify with real competition submission after deploying.
