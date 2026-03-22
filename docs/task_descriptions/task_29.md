# Task 29: Project Lifecycle

**Tier:** 3 (max 6.0 points)
**Score max:** 11 points raw, 7 checks
**Current best:** 1.6364 (6/11, checks 3,4,5 failing)

## What the task asks

Execute a complete project lifecycle:
1. Create a fixed-price project with a budget, linked to a customer
2. Log timesheet hours for TWO employees (project manager + consultant)
3. Register a supplier cost from a named supplier
4. Create a customer invoice for the project

All entities (customer, employees, supplier) are pre-created in the sandbox.

## Prompt variants

- Norwegian (Bokmål/Nynorsk), English, German, Spanish, Portuguese, French
- Different project names, budgets, employee names/hours, supplier costs each time
- Always TWO employees with different roles (PM + consultant/advisor)

## Competition checks (inferred from 13 runs)

| Check | Points | Status | Likely tests |
|-------|--------|--------|-------------|
| 1 | 2 | PASS | Project exists with correct name |
| 2 | 1 | PASS | Project has customer linked |
| 3 | 1 | FAIL | Timesheet entries / project participants |
| 4 | 2 | FAIL | Hours correct per employee |
| 5 | 2 | FAIL | Supplier cost amount on expense account |
| 6 | 2 | PASS | Customer invoice exists |
| 7 | 1 | PASS | Invoice amount > 0 |

## Known issues (as of revision 00069-t8m)

1. **Consultant not added as project participant**: Only PM is auto-added via `projectManager` field. The second employee (consultant) is never added via `POST /project/participant`. This likely causes checks 3 and 4 to fail.

2. **Supplier cost VAT splitting**: Agent uses `vatType:{id:1}` (25% MVA) on the expense posting, which makes Tripletex split the amount: only 80% goes to account 4300, the rest to VAT account 2710. If check 5 verifies the full supplier cost amount on account 4300, it fails because only 48600 is there instead of 60750.

## Fixes applied

- Added `POST /project/participant` step to golden path (add consultant after project creation)
- Changed supplier cost posting to omit vatType (full amount stays on expense account)
- Updated key_lessons to explicitly warn against vatType on supplier cost

## API notes

- `POST /project/participant` works (not blocked as BETA), takes `{project:{id}, employee:{id}}`
- With `vatType:{id:1}`, amountGross 60750 → net 48600 on expense + 12150 on VAT account
- Without vatType, full 60750 stays on expense account
- Both timesheet entries are accepted even without the consultant being a participant
