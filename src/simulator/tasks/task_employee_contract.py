"""Task: Create employee with full employment details (simulates task_01/task_19)."""

import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class EmployeeContractTask(BaseTask):
    """Tier 2 task: Create employee with employment record including salary and occupation code.

    Tests the full employee creation flow: employee + employment + employment details.
    """

    name = "Create Employee (Full)"
    tier = 2
    optimal_calls = 4  # dept + occupationCode + POST employee + POST employment

    prompts = [
        "Opprett den ansatte Kari Nordmann med fødselsdato 15. mars 1990, avdeling Salg, stillingskode 2320, årslønn 520000 kr, stillingsprosent 100%, startdato 1. april 2026. E-post: kari.nordmann@example.org.",
        "Create the employee Erik Olsen born 22 June 1985, department IT, occupation code 3120, annual salary 680000 NOK, 80% employment, start date 1 May 2026. Email: erik.olsen@example.org.",
        "Erstellen Sie den Mitarbeiter Lars Berg, geboren am 10. Januar 1992, Abteilung Økonomi, Stellencode 2411, Jahresgehalt 450000 NOK, 100% Beschäftigung, Startdatum 1. Juni 2026. E-Mail: lars.berg@example.org.",
    ]

    def extract_expected(self, prompt: str) -> dict:
        result = {}

        # Extract email
        email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', prompt)
        if email_match:
            result["email"] = email_match.group(0)

        # Extract name — after "ansatte/employee/Mitarbeiter"
        name_patterns = [
            r'(?:ansatte|tilsett)\s+(\w+\s+\w+)',
            r'(?:employee)\s+(\w+\s+\w+)',
            r'(?:Mitarbeiter)\s+(\w+\s+\w+)',
        ]
        for pat in name_patterns:
            m = re.search(pat, prompt, re.IGNORECASE)
            if m:
                parts = m.group(1).split()
                result["firstName"] = parts[0]
                result["lastName"] = parts[1] if len(parts) > 1 else ""
                break

        # Extract department
        dept_patterns = [
            r'(?:avdeling|department|Abteilung)\s+(\w+)',
        ]
        for pat in dept_patterns:
            m = re.search(pat, prompt, re.IGNORECASE)
            if m:
                result["department"] = m.group(1)
                break

        # Extract annual salary
        salary_match = re.search(r'(\d{4,7})\s*(?:kr|NOK)', prompt)
        if salary_match:
            result["salary"] = float(salary_match.group(1))

        # Extract percentage
        pct_match = re.search(r'(\d{2,3})\s*%', prompt)
        if pct_match:
            result["percentage"] = float(pct_match.group(1))

        # Extract occupation code
        occ_match = re.search(r'(?:stillingskode|occupation code|Stellencode)\s+(\d{4})', prompt, re.IGNORECASE)
        if occ_match:
            result["occupationCode"] = occ_match.group(1)

        return result

    def setup(self, base_url: str, session_token: str, expected: dict):
        """Ensure the department exists."""
        dept_name = expected.get("department", "")
        if not dept_name:
            return

        resp = self._api(base_url, session_token, "GET", "/department", {
            "name": dept_name, "fields": "id,name", "count": 1,
        })
        if resp.get("values"):
            print(f"  Department '{dept_name}' exists")
        else:
            self._api(base_url, session_token, "POST", "/department", json_body={
                "name": dept_name,
            })
            print(f"  Created department '{dept_name}'")

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        email = expected.get("email", "")
        first_name = expected.get("firstName", "")
        last_name = expected.get("lastName", "")

        # Find employee by email
        resp = verifier.get("/employee", {
            "email": email,
            "fields": "id,firstName,lastName,email,dateOfBirth,department(*),employments(*)",
            "count": 1,
        })
        employees = resp.get("values", [])
        employee = employees[0] if employees else None

        checks.append(Check(
            name="Employee found",
            passed=employee is not None,
            expected=f"{first_name} {last_name} ({email})",
            actual=f"{employee.get('firstName', '')} {employee.get('lastName', '')}" if employee else "NOT FOUND",
            points=2,
        ))

        if not employee:
            return checks

        # Check name
        checks.append(Check(
            name="First name correct",
            passed=employee.get("firstName", "").lower() == first_name.lower(),
            expected=first_name,
            actual=employee.get("firstName", ""),
        ))
        checks.append(Check(
            name="Last name correct",
            passed=employee.get("lastName", "").lower() == last_name.lower(),
            expected=last_name,
            actual=employee.get("lastName", ""),
        ))

        # Check department
        dept = employee.get("department") or {}
        dept_name = expected.get("department", "")
        if dept_name:
            actual_dept = dept.get("name", "")
            checks.append(Check(
                name="Department correct",
                passed=actual_dept.lower() == dept_name.lower(),
                expected=dept_name,
                actual=actual_dept or "NONE",
            ))

        # Check employments exist
        employments = employee.get("employments", [])
        checks.append(Check(
            name="Employment record created",
            passed=len(employments) > 0,
            expected="at least 1 employment",
            actual=f"{len(employments)} employments",
            points=2,
        ))

        # Check employment details (salary, percentage) via separate call
        if employments:
            emp_id = employments[0].get("id")
            if emp_id:
                emp_resp = verifier.get(f"/employee/employment/{emp_id}", {
                    "fields": "id,startDate,employmentDetails(*)",
                })
                emp_val = emp_resp.get("value", {})
                details = emp_val.get("employmentDetails", [])
                if details:
                    detail = details[0] if isinstance(details[0], dict) else {}
                    # Check salary
                    if "salary" in expected:
                        actual_salary = detail.get("annualSalary", 0)
                        checks.append(Check(
                            name="Annual salary correct",
                            passed=abs(actual_salary - expected["salary"]) < 100,
                            expected=str(expected["salary"]),
                            actual=str(actual_salary),
                        ))
                    # Check percentage
                    if "percentage" in expected:
                        actual_pct = detail.get("percentageOfFullTimeEquivalent", 0)
                        checks.append(Check(
                            name="Employment percentage correct",
                            passed=abs(actual_pct - expected["percentage"]) < 1,
                            expected=str(expected["percentage"]),
                            actual=str(actual_pct),
                        ))

        return checks
