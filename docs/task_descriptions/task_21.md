# Task 21: Employee Onboarding from Offer Letter PDF

**Tier:** 3 (max 6.0 points)
**Score max:** 11 points raw, 7 checks
**Current best:** 2.5714 (9/11 in best run)

## What the task asks

Receive an offer letter PDF (tilbudsbrev) for a new employee. Perform complete onboarding:
1. Create the employee
2. Assign correct department (create if needed)
3. Set up employment details (percentage, salary, occupation code)
4. Configure standard working time

## Prompt variants

- German ("Angebotsschreiben"), Norwegian ("tilbudsbrev"), other languages
- PDF contains: name, DOB, department, position title, start date, FTE%, salary, hours/day
- PDF does NOT contain email — must be generated

## Competition checks (inferred)

| Check | Points | Status | Likely tests |
|-------|--------|--------|-------------|
| 1 | ? | PASS | Employee exists with correct name |
| 2 | ? | PASS | Employee has correct DOB |
| 3 | ? | FAIL | Department has departmentNumber set |
| 4 | ? | FAIL | Employee has email address |
| 5 | ? | FAIL | Some employment detail (occupation code?) |
| 6 | ? | PASS | Employment percentage and salary correct |
| 7 | ? | PASS | Standard working time set |

## Known issues (as of revision 00069-t8m)

1. **Missing departmentNumber**: Agent creates department without `departmentNumber` field. Competition checks for it. The run that scored 2.57 (00056-djv) set `departmentNumber: "2"`.

2. **Missing email**: The validator auto-fix (Rule 16) was setting `userType: NO_ACCESS` when no email — this prevented the agent from ever generating an email. The 2.57 run generated `leon.meyer@company.no` after getting a 422 error.

## Fixes applied

- Validator Rule 16 now auto-generates email as `firstname.lastname@company.no` with Norwegian char normalization (ø→o, å→a, æ→ae) and sets `userType: STANDARD`
- Validator Rule 19 auto-sets `departmentNumber: "1"` on POST /department when missing
- Playbook updated to mention departmentNumber requirement

## Aliases

Task 21 uses the task_19 playbook (same employee onboarding flow).
