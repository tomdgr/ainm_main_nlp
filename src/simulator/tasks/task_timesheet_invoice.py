"""Task 16: Log hours on a project activity and create a project invoice."""

import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class TimesheetInvoiceTask(BaseTask):
    name = "Log Hours + Project Invoice"
    tier = 2
    optimal_calls = 7  # customer + employee + project + activity + timesheet + order + invoice

    prompts = [
        'Registrer 24 timer for Kari Hansen (kari.hansen@example.org) på aktiviteten "Utvikling" i prosjektet "Nettbutikk" for Nordlys AS (org.nr 934567890). Timesats: 1200 NOK/t. Opprett en prosjektfaktura til kunden basert på registrerte timer.',
        'Log 16 hours for John Smith (john.smith@example.org) on the activity "Consulting" in the project "Cloud Setup" for Clearwater Ltd (org no. 891234567). Hourly rate: 1800 NOK/h. Create a project invoice to the customer based on the logged hours.',
    ]

    def extract_expected(self, prompt: str) -> dict:
        result = {}

        hours_match = re.search(r'(\d+)\s*(?:timer|hours|Stunden|heures|horas)', prompt, re.IGNORECASE)
        if hours_match:
            result["hours"] = int(hours_match.group(1))

        rate_match = re.search(r'(\d+)\s*(?:NOK/t|NOK/h|NOK/Std)', prompt)
        if rate_match:
            result["hourly_rate"] = int(rate_match.group(1))

        email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', prompt)
        if email_match:
            result["employee_email"] = email_match.group(0)

        org_match = re.search(r'\b(\d{9})\b', prompt)
        if org_match:
            result["organizationNumber"] = org_match.group(1)

        # Extract project name (quoted)
        quotes = re.findall(r'["\u201c]([^"\u201d]+)["\u201d]', prompt)
        if len(quotes) >= 2:
            result["activity_name"] = quotes[0]
            result["project_name"] = quotes[1]
        elif len(quotes) == 1:
            result["project_name"] = quotes[0]

        cust_match = re.search(r'(?:for|für)\s+(.+?)(?:\s*\()', prompt, re.IGNORECASE)
        if cust_match:
            result["customer_name"] = cust_match.group(1).strip()

        if "hours" in result and "hourly_rate" in result:
            result["expected_total"] = result["hours"] * result["hourly_rate"]

        return result

    def setup(self, base_url: str, session_token: str, expected: dict):
        """Pre-create customer and employee."""
        org_nr = expected.get("organizationNumber", "")
        name = expected.get("customer_name", "Test Customer")
        email = expected.get("employee_email", "")

        # Ensure bank account
        resp = self._api(base_url, session_token, "GET", "/ledger/account", {
            "number": "1920", "fields": "id,version,bankAccountNumber",
        })
        accounts = resp.get("values", [])
        if accounts and not accounts[0].get("bankAccountNumber"):
            self._api(base_url, session_token, "PUT", f"/ledger/account/{accounts[0]['id']}", json_body={
                "id": accounts[0]["id"], "version": accounts[0]["version"],
                "bankAccountNumber": "12345678903",
            })

        # Ensure customer
        resp = self._api(base_url, session_token, "GET", "/customer", {
            "organizationNumber": org_nr, "fields": "id", "count": 1,
        })
        if not resp.get("values"):
            self._api(base_url, session_token, "POST", "/customer", json_body={
                "name": name, "organizationNumber": org_nr, "isCustomer": True,
            })
            print(f"  Created customer: {name}")

        # Ensure employee
        if email:
            resp = self._api(base_url, session_token, "GET", "/employee", {
                "email": email, "fields": "id", "count": 1,
            })
            if not resp.get("values"):
                dept_resp = self._api(base_url, session_token, "GET", "/department", {
                    "fields": "id", "count": 1,
                })
                dept_id = dept_resp.get("values", [{}])[0].get("id")
                parts = email.split("@")[0].split(".")
                self._api(base_url, session_token, "POST", "/employee", json_body={
                    "firstName": parts[0].capitalize(),
                    "lastName": parts[1].capitalize() if len(parts) > 1 else "Worker",
                    "email": email, "userType": "STANDARD",
                    "department": {"id": dept_id} if dept_id else None,
                })
                print(f"  Created employee: {email}")

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        project_name = expected.get("project_name", "")
        hours = expected.get("hours", 0)
        org_nr = expected.get("organizationNumber", "")

        # Find the project
        resp = verifier.get("/project", {"fields": "id,name,customer(*)", "count": 50})
        projects = resp.get("values", [])
        project = next((p for p in projects if p.get("name", "").lower() == project_name.lower()), None)

        checks.append(Check(
            name="Project found",
            passed=project is not None,
            expected=project_name,
            actual=project.get("name", "NOT FOUND") if project else "NOT FOUND",
            points=2,
        ))

        if not project:
            return checks

        project_id = project["id"]

        # Check timesheet entries
        resp = verifier.get("/timesheet/entry", {
            "projectId": project_id,
            "dateFrom": "2020-01-01", "dateTo": "2099-12-31",
            "fields": "id,hours,employee(*),activity(*)",
            "count": 50,
        })
        entries = resp.get("values", [])
        total_hours = sum(e.get("hours", 0) for e in entries)

        checks.append(Check(
            name="Timesheet entries logged",
            passed=len(entries) > 0,
            expected=f"{hours} hours",
            actual=f"{total_hours} hours in {len(entries)} entries",
            points=2,
        ))

        checks.append(Check(
            name="Correct hours logged",
            passed=abs(total_hours - hours) < 1,
            expected=str(hours),
            actual=str(total_hours),
            points=2,
        ))

        # Check invoice created
        customer = project.get("customer", {})
        customer_id = customer.get("id")
        if customer_id:
            resp = verifier.get("/invoice", {
                "customerId": customer_id,
                "invoiceDateFrom": "2020-01-01", "invoiceDateTo": "2099-12-31",
                "fields": "id,amountExcludingVat,isCreditNote", "count": 10,
            })
            invoices = [inv for inv in resp.get("values", []) if not inv.get("isCreditNote")]
            checks.append(Check(
                name="Project invoice created",
                passed=len(invoices) > 0,
                expected="at least 1 invoice",
                actual=f"{len(invoices)} invoices",
                points=3,
            ))

        return checks
