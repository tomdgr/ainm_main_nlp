"""Task 12: Process payroll — base salary + bonus for an employee."""

import datetime
import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class PayrollTask(BaseTask):
    """Tier 2 task: Process payroll for an employee with base salary and bonus.

    Complex prerequisite chain: employee must have dateOfBirth, employment,
    employment details, and a division before a salary transaction can be created.

    Setup pre-creates the employee (with email). The agent must then:
    1. Set dateOfBirth on the employee (always null initially)
    2. Create employment record
    3. Create employment details (salary, type, etc.)
    4. Create/find a division and link it to the employment
    5. Create salary transaction with base salary + bonus specifications
    """

    name = "Process Payroll"
    tier = 2
    optimal_calls = 10  # GET employee + GET salary/type + GET employment + PUT employee (dob) + POST employment + POST employment/details + GET municipality + GET whoAmI + POST division + PUT employment (link div) + POST salary/transaction

    prompts = [
        "Process payroll for Erik Hansen (erik.hansen@example.org) for this month. Base salary is 45000 NOK. Add a one-time bonus of 8000 NOK.",
        "Kjor lonn for Maria Olsen (maria.olsen@example.org) denne maneden. Grunnlonn er 52000 NOK. Legg til en engangsbonus pa 6500 NOK.",
        "Executez la paie de Jean Dupont (jean.dupont@example.org) pour ce mois. Le salaire de base est de 48000 NOK. Ajoutez une prime unique de 7200 NOK en plus du salaire de base.",
    ]

    def extract_expected(self, prompt: str) -> dict:
        result = {}

        # Extract email
        email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', prompt)
        if email_match:
            result["email"] = email_match.group(0)

        # Extract name — look for patterns like "for NAME (email)" or "for NAME med"
        name_patterns = [
            r'(?:for|de)\s+([A-Z\u00C0-\u024F][a-z\u00C0-\u024F]+\s+[A-Z\u00C0-\u024F][a-z\u00C0-\u024F]+)',
        ]
        for pat in name_patterns:
            m = re.search(pat, prompt)
            if m:
                parts = m.group(1).split()
                result["firstName"] = parts[0]
                result["lastName"] = parts[1] if len(parts) > 1 else ""
                break

        # Extract all NOK amounts — first is base salary, second is bonus
        amounts = re.findall(r'(\d[\d\s]*\d)\s*(?:NOK|kr)', prompt)
        if not amounts:
            amounts = re.findall(r'(\d{4,6})\s*(?:NOK|kr)', prompt)
        cleaned = [float(a.replace(" ", "").replace("\u00a0", "")) for a in amounts]

        if len(cleaned) >= 2:
            result["base_salary"] = cleaned[0]
            result["bonus"] = cleaned[1]
        elif len(cleaned) == 1:
            result["base_salary"] = cleaned[0]
            result["bonus"] = 0

        return result

    def setup(self, base_url: str, session_token: str, expected: dict):
        """Pre-create the employee so the agent can find them by email."""
        email = expected.get("email", "")
        first_name = expected.get("firstName", "Test")
        last_name = expected.get("lastName", "Employee")

        if not email:
            print("  WARNING: No email extracted from prompt")
            return

        print(f"  Setting up payroll task: employee={first_name} {last_name} ({email})")

        # Check if employee already exists
        resp = self._api(base_url, session_token, "GET", "/employee", {
            "email": email, "fields": "id,firstName,lastName,email,dateOfBirth", "count": 1,
        })
        employees = resp.get("values", [])

        if employees:
            emp = employees[0]
            expected["_employee_id"] = emp["id"]
            print(f"  Employee already exists: id={emp['id']}")

            # Clean up: remove any existing employments to give agent a clean slate
            emp_resp = self._api(base_url, session_token, "GET", "/employee/employment", {
                "employeeId": emp["id"], "fields": "id", "count": 10,
            })
            existing_employments = emp_resp.get("values", [])
            if existing_employments:
                print(f"  Employee has {len(existing_employments)} existing employment(s)")
        else:
            # Create employee — deliberately leave dateOfBirth as null
            # (the agent must set it before creating employment)
            resp = self._api(base_url, session_token, "POST", "/employee", json_body={
                "firstName": first_name,
                "lastName": last_name,
                "email": email,
            })
            emp = resp.get("value", {})
            if emp.get("id"):
                expected["_employee_id"] = emp["id"]
                print(f"  Created employee: id={emp['id']}")
            else:
                print(f"  ERROR: Failed to create employee: {resp}")

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        email = expected.get("email", "")
        first_name = expected.get("firstName", "")
        last_name = expected.get("lastName", "")
        base_salary = expected.get("base_salary", 0)
        bonus = expected.get("bonus", 0)

        # ---- Check 1: Employee exists with correct email ----
        resp = verifier.get("/employee", {
            "email": email,
            "fields": "id,firstName,lastName,email,dateOfBirth,employments(*)",
            "count": 1,
        })
        employees = resp.get("values", [])
        employee = employees[0] if employees else None

        checks.append(Check(
            name="Employee found",
            passed=employee is not None,
            expected=f"{first_name} {last_name} ({email})",
            actual=f"{employee.get('firstName', '')} {employee.get('lastName', '')}" if employee else "NOT FOUND",
            points=1,
        ))

        if not employee:
            return checks

        emp_id = employee["id"]

        # ---- Check 2: dateOfBirth was set (required for employment) ----
        dob = employee.get("dateOfBirth")
        checks.append(Check(
            name="Date of birth set",
            passed=dob is not None and dob != "",
            expected="a valid date",
            actual=str(dob) if dob else "null",
            points=1,
        ))

        # ---- Check 3: Employment record exists ----
        employments = employee.get("employments", [])
        emp_resp = verifier.get("/employee/employment", {
            "employeeId": emp_id,
            "fields": "id,startDate,division(*),employmentDetails(*)",
            "count": 5,
        })
        employments_detail = emp_resp.get("values", [])

        has_employment = len(employments_detail) > 0
        checks.append(Check(
            name="Employment record created",
            passed=has_employment,
            expected="at least 1 employment",
            actual=f"{len(employments_detail)} employment(s)",
            points=2,
        ))

        if not has_employment:
            return checks

        employment = employments_detail[0]
        employment_id = employment.get("id")

        # ---- Check 4: Employment has a division linked ----
        division = employment.get("division")
        has_division = division is not None and isinstance(division, dict) and division.get("id")
        checks.append(Check(
            name="Division linked to employment",
            passed=bool(has_division),
            expected="division with id",
            actual=f"division={division}" if division else "NONE",
            points=1,
        ))

        # ---- Check 5: Employment details exist with salary info ----
        details = employment.get("employmentDetails", [])
        if not details and employment_id:
            # Try fetching details separately
            det_resp = verifier.get(f"/employee/employment/{employment_id}", {
                "fields": "id,employmentDetails(*)",
            })
            details = det_resp.get("value", {}).get("employmentDetails", [])

        has_details = len(details) > 0
        checks.append(Check(
            name="Employment details created",
            passed=has_details,
            expected="at least 1 detail record",
            actual=f"{len(details)} detail(s)",
            points=1,
        ))

        # ---- Check 6: Salary transaction exists ----
        # This is the ultimate goal — check if a salary transaction was posted
        today = datetime.date.today()
        year = today.year
        month = today.month

        # Try multiple date ranges to find the transaction
        sal_resp = verifier.get("/salary/transaction", {
            "dateFrom": f"{year}-{month:02d}-01",
            "dateTo": f"{year}-{month:02d}-28",
            "fields": "id,date,year,month,payslips(*)",
            "count": 10,
        })
        transactions = sal_resp.get("values", [])

        # Also try without date filter in case agent used different dates
        if not transactions:
            sal_resp = verifier.get("/salary/transaction", {
                "dateFrom": f"{year}-01-01",
                "dateTo": f"{year}-12-31",
                "fields": "id,date,year,month,payslips(*)",
                "count": 10,
            })
            transactions = sal_resp.get("values", [])

        # Check if any transaction has a payslip for our employee
        found_transaction = False
        found_base = False
        found_bonus = False

        for txn in transactions:
            payslips = txn.get("payslips", [])
            for ps in payslips:
                ps_emp = ps.get("employee", {})
                if ps_emp.get("id") == emp_id:
                    found_transaction = True
                    # Check specifications for base salary and bonus
                    specs = ps.get("specifications", [])
                    for spec in specs:
                        amount = spec.get("amount", 0)
                        if amount and abs(amount - base_salary) < 1:
                            found_base = True
                        if bonus and amount and abs(amount - bonus) < 1:
                            found_bonus = True

        checks.append(Check(
            name="Salary transaction created",
            passed=found_transaction,
            expected=f"transaction for employee {emp_id}",
            actual="FOUND" if found_transaction else "NOT FOUND (sandbox may not support this fully)",
            points=2,
        ))

        if found_transaction:
            checks.append(Check(
                name="Base salary amount correct",
                passed=found_base,
                expected=str(base_salary),
                actual="FOUND" if found_base else "NOT FOUND in specifications",
                points=1,
            ))
            if bonus:
                checks.append(Check(
                    name="Bonus amount correct",
                    passed=found_bonus,
                    expected=str(bonus),
                    actual="FOUND" if found_bonus else "NOT FOUND in specifications",
                    points=1,
                ))

        return checks
