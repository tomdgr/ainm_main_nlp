"""Task 15: Set fixed price on project and invoice a partial amount."""

import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class FixedPriceProjectTask(BaseTask):
    name = "Fixed Price Project + Partial Invoice"
    tier = 2
    optimal_calls = 6  # customer + employee + project + order + invoice

    prompts = [
        'Sett fastpris 250000 kr på prosjektet "Datamigrering" for Nordlys AS (org.nr 934567890). Prosjektleiar er Kari Hansen (kari.hansen@example.org). Fakturer kunden for 50 % av fastprisen som ei delbetaling.',
        'Set a fixed price of 180000 NOK on the project "Cloud Migration" for Clearwater Ltd (org no. 891234567). The project manager is John Smith (john.smith@example.org). Invoice the customer for 75% of the fixed price as a partial payment.',
    ]

    def extract_expected(self, prompt: str) -> dict:
        result = {}

        org_match = re.search(r'\b(\d{9})\b', prompt)
        if org_match:
            result["organizationNumber"] = org_match.group(1)

        price_match = re.search(r'(\d[\d\s]*\d)\s*(?:kr|NOK)', prompt)
        if price_match:
            result["fixed_price"] = float(price_match.group(1).replace(" ", ""))

        pct_match = re.search(r'(\d{2,3})\s*%', prompt)
        if pct_match:
            result["invoice_pct"] = float(pct_match.group(1))

        name_match = re.search(r'["\u201c]([^"\u201d]+)["\u201d]', prompt)
        if name_match:
            result["project_name"] = name_match.group(1)

        email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', prompt)
        if email_match:
            result["manager_email"] = email_match.group(0)

        cust_match = re.search(r'(?:for|für)\s+(.+?)(?:\s*\()', prompt, re.IGNORECASE)
        if cust_match:
            result["customer_name"] = cust_match.group(1).strip()

        if "fixed_price" in result and "invoice_pct" in result:
            result["invoice_amount"] = result["fixed_price"] * result["invoice_pct"] / 100

        return result

    def setup(self, base_url: str, session_token: str, expected: dict):
        """Pre-create customer and employee."""
        org_nr = expected.get("organizationNumber", "")
        name = expected.get("customer_name", "Test Customer")
        email = expected.get("manager_email", "")

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

        # Ensure employee (project manager)
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
                    "lastName": parts[1].capitalize() if len(parts) > 1 else "Manager",
                    "email": email, "userType": "STANDARD",
                    "department": {"id": dept_id} if dept_id else None,
                })
                print(f"  Created employee: {email}")

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        project_name = expected.get("project_name", "")
        invoice_amount = expected.get("invoice_amount", 0)

        # Find the project
        resp = verifier.get("/project", {"fields": "id,name,isFixedPrice,fixedprice,customer(*)", "count": 50})
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

        # Check fixed price set
        checks.append(Check(
            name="Fixed price set on project",
            passed=project.get("isFixedPrice", False) is True,
            expected="isFixedPrice=true",
            actual=f"isFixedPrice={project.get('isFixedPrice')}",
            points=2,
        ))

        fp = expected.get("fixed_price", 0)
        actual_fp = project.get("fixedprice", 0)
        checks.append(Check(
            name="Fixed price amount correct",
            passed=abs(actual_fp - fp) < 100,
            expected=str(fp),
            actual=str(actual_fp),
        ))

        # Check invoice created for partial amount
        customer = project.get("customer", {})
        customer_id = customer.get("id")
        if customer_id:
            resp = verifier.get("/invoice", {
                "customerId": customer_id,
                "invoiceDateFrom": "2020-01-01", "invoiceDateTo": "2099-12-31",
                "fields": "id,amountExcludingVat,isCreditNote", "count": 10,
            })
            invoices = [inv for inv in resp.get("values", []) if not inv.get("isCreditNote")]
            partial_match = any(abs(inv.get("amountExcludingVat", 0) - invoice_amount) < 100 for inv in invoices)

            checks.append(Check(
                name=f"Partial invoice ({expected.get('invoice_pct', 0):.0f}% = {invoice_amount:.0f} NOK)",
                passed=partial_match,
                expected=f"~{invoice_amount:.0f} NOK",
                actual=f"{len(invoices)} invoices found",
                points=3,
            ))

        return checks
