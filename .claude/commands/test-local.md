Run the local game simulator to smoke-test agent changes before deployment.

## Prerequisites

The local HTTPS server must be running in another terminal:
```
./run_local.sh
```

## Usage

Run the test script with specific tasks or all tasks:

```bash
python test_local.py                        # all tasks (task_1, task_2, task_4, task_6, task_7, task_8)
python test_local.py task_1 task_2 task_4    # specific tasks (fast — tier 1 only)
python test_local.py task_6 task_7 task_8    # tier 2 tasks (slower, more complex)
```

## Available simulator tasks

| Sim Task | Real Task | Name | Tier | What it tests |
|----------|-----------|------|------|---------------|
| task_1 | ~01-03 | Create Departments | 1 | Create 3 departments by name |
| task_2 | ~02 | Create Customer | 1 | Customer with name, org number, address, email |
| task_4 | ~04 | Create Supplier | 1 | Supplier via /supplier endpoint |
| task_6 | ~06 | Create & Send Invoice | 2 | Customer + product + order + invoice + send |
| task_7 | ~07 | Register Payment | 2 | Find existing invoice, register full payment |
| task_8 | ~08 | Create Project | 2 | Project with customer and project manager |

## What to look for in the output

### 1. Simulator report
The table at the end shows checks passed, API calls, errors, and scores per task. A regression shows as failed checks or new API errors.

### 2. Run log analysis
After the simulator report, the script scans newly created log files and prints:
- **`[VALIDATION]` catches**: Pre-validator blocked a bad call (saved an API call). This is the validator working correctly.
- **`[API] -> 4xx` errors**: Real API errors that got through. These cost points.
- **Clean run**: No warnings and no errors — ideal.

### 3. Key signals

| Signal | Meaning |
|--------|---------|
| All checks pass, 0 errors | Changes are safe to deploy |
| Checks pass but new API errors | Agent works but efficiency dropped — investigate |
| Validation catches + checks pass | Validator saved API calls — good |
| Failed checks | Regression — do NOT deploy, investigate |
| Task errors (exceptions) | Agent crashed — fix before deploying |

## Reading the full run logs

After running, logs are at:
```
src/logs/runs/local/{task_id}/no_0_YYYYMMDD_HHMMSS_run.txt     # agent decisions + API calls
src/logs/runs/local/{task_id}/no_0_YYYYMMDD_HHMMSS_console.txt  # Python logging output
```

## Important caveats

- The simulator uses a **shared sandbox account** — state accumulates between runs. Some tasks may behave differently on a fresh account (like the competition provides).
- The simulator covers only 6 of 30 task types. It cannot catch regressions in tasks it doesn't simulate (e.g., supplier invoices, travel expenses, credit notes).
- Use this as a **pre-validation step** that things aren't broken, NOT as ground truth for scoring. Always verify with a real competition submission after deploying.
