"""Task: Create project linked to customer and project manager."""

import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class ProjectTask(BaseTask):
    name = "Create Project"
    tier = 2
    optimal_calls = 3  # find/create customer + find employee + create project

    prompts = [
        'Erstellen Sie das Projekt "Analyse Windkraft" verknüpft mit dem Kunden Windkraft GmbH (Org.-Nr. 897356171). Projektleiter ist Finn Richter (finn.richter@example.org).',
        'Crea el proyecto "Análisis Costa" vinculado al cliente Costa Brava SL (org. nº 921937946). El director del proyecto es Isabel González (isabel.gonzalez@example.org).',
        'Create the project "Analysis Ridgepoint" linked to the customer Ridgepoint Ltd (org no. 987409339). The project manager is Alice Harris (alice.harris@example.org).',
    ]

    def extract_expected(self, prompt: str) -> dict:
        result = {}

        name_match = re.search(r'["\u201c]([^"\u201d]+)["\u201d]', prompt)
        if name_match:
            result["project_name"] = name_match.group(1)

        org_match = re.search(r'\b(\d{9})\b', prompt)
        if org_match:
            result["organizationNumber"] = org_match.group(1)

        name_patterns = [
            r'(?:customer|Kunden?|cliente?|client)\s+(.+?)(?:\s*\()',
        ]
        for pattern in name_patterns:
            m = re.search(pattern, prompt, re.IGNORECASE)
            if m:
                result["customer_name"] = m.group(1).strip().strip("'\"")
                break

        email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', prompt)
        if email_match:
            result["manager_email"] = email_match.group(0)

        manager_patterns = [
            r'(?:manager is|Projektleiter ist|director.*es|gerente.*é)\s+(.+?)(?:\s*\()',
            r'(?:manager is|Projektleiter ist|director.*es|gerente.*é)\s+(\w+\s+\w+)',
        ]
        for pattern in manager_patterns:
            m = re.search(pattern, prompt, re.IGNORECASE)
            if m:
                result["manager_name"] = m.group(1).strip()
                break

        return result

    def setup(self, base_url: str, session_token: str, expected: dict):
        """Pre-create customer and employee with project manager entitlements."""
        org_nr = expected.get("organizationNumber", "")
        customer_name = expected.get("customer_name", "Test Customer")
        manager_email = expected.get("manager_email", "")
        manager_name = expected.get("manager_name", "Test Manager")

        print(f"  Setting up project task: customer={customer_name}, manager={manager_name}")

        # Check/create customer
        resp = self._api(base_url, session_token, "GET", "/customer", {
            "organizationNumber": org_nr, "fields": "id,name", "count": 1,
        })
        customers = resp.get("values", [])
        if customers:
            print(f"  Customer already exists: id={customers[0]['id']}")
        else:
            resp = self._api(base_url, session_token, "POST", "/customer", json_body={
                "name": customer_name, "organizationNumber": org_nr, "isCustomer": True,
            })
            cid = resp.get("value", {}).get("id")
            print(f"  Created customer: id={cid}")

        # Check/create employee
        resp = self._api(base_url, session_token, "GET", "/employee", {
            "email": manager_email, "fields": "id,firstName,lastName,email", "count": 1,
        })
        employees = resp.get("values", [])

        if employees:
            employee_id = employees[0]["id"]
            print(f"  Employee already exists: id={employee_id}")
        else:
            # Need a department
            resp = self._api(base_url, session_token, "GET", "/department", {
                "fields": "id", "count": 1,
            })
            dept_id = resp.get("values", [{}])[0].get("id")

            # Parse first/last name
            parts = manager_name.split(maxsplit=1)
            first_name = parts[0] if parts else "Test"
            last_name = parts[1] if len(parts) > 1 else "Manager"

            resp = self._api(base_url, session_token, "POST", "/employee", json_body={
                "firstName": first_name,
                "lastName": last_name,
                "email": manager_email,
                "userType": "STANDARD",
                "department": {"id": dept_id},
            })
            employee_id = resp.get("value", {}).get("id")
            print(f"  Created employee: id={employee_id}")

        if not employee_id:
            print("  ERROR: Failed to create employee")
            return

        # Grant entitlements via template (so they can be project manager)
        resp = self._api(base_url, session_token, "PUT",
                         "/employee/entitlement/:grantEntitlementsByTemplate",
                         params={"employeeId": employee_id, "template": "allTripletexAdministrator"})
        if resp:
            print(f"  Granted admin entitlements to employee {employee_id}")
        else:
            print(f"  WARNING: Failed to grant entitlements to employee {employee_id}")

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        project_name = expected.get("project_name", "")
        org_nr = expected.get("organizationNumber", "")
        manager_email = expected.get("manager_email", "")

        resp = verifier.get("/project", {
            "fields": "id,name,customer(*),projectManager(*),startDate",
            "count": 50,
        })
        projects = resp.get("values", [])
        project = None
        for p in projects:
            if p.get("name", "").lower() == project_name.lower():
                project = p
                break

        checks.append(Check(
            name="Project found",
            passed=project is not None,
            expected=project_name,
            actual=project.get("name", "NOT FOUND") if project else "NOT FOUND",
            points=2,
        ))

        if not project:
            return checks

        checks.append(Check(
            name="Project name matches",
            passed=project.get("name", "").lower() == project_name.lower(),
            expected=project_name,
            actual=project.get("name", ""),
        ))

        customer = project.get("customer") or {}
        checks.append(Check(
            name="Customer linked",
            passed=customer.get("organizationNumber") == org_nr,
            expected=f"org={org_nr}",
            actual=f"org={customer.get('organizationNumber', 'NONE')}",
        ))

        if "customer_name" in expected:
            checks.append(Check(
                name="Customer name matches",
                passed=customer.get("name", "").lower() == expected["customer_name"].lower(),
                expected=expected["customer_name"],
                actual=customer.get("name", ""),
            ))

        manager = project.get("projectManager") or {}
        checks.append(Check(
            name="Project manager linked",
            passed=manager.get("id") is not None and manager.get("id") != 0,
            expected="employee assigned",
            actual=f"id={manager.get('id', 'NONE')}",
        ))

        if manager_email:
            checks.append(Check(
                name="Manager email matches",
                passed=manager.get("email", "").lower() == manager_email.lower(),
                expected=manager_email,
                actual=manager.get("email", ""),
            ))

        checks.append(Check(
            name="Start date set",
            passed=project.get("startDate") is not None and project.get("startDate") != "",
            expected="date set",
            actual=project.get("startDate", "NONE"),
        ))

        return checks
