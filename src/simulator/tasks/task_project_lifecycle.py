"""Task 29: Full project lifecycle — budget + hours + supplier costs + customer invoice.

NOTE: These checks are APPROXIMATIONS. The real competition scorer likely checks additional
fields (per-employee hours, hourly rates, project-invoice linkage, specific account numbers)
that this simulator does not verify. Local pass ≠ competition pass.
"""

import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class ProjectLifecycleTask(BaseTask):
    name = "Full Project Lifecycle"
    tier = 3
    optimal_calls = 10  # lookups + project + activity + timesheet + voucher + bank acct + invoice

    prompts = [
        'Opprett prosjektet "Systemutvikling" for kunden Nordfjord AS (org.nr 987654001) med et budsjett på 150000 NOK. Registrer 40 timer for Ole Hansen (ole.hansen@example.org) med timesats 1200 NOK/t. Registrer leverandørkostnad fra Fjellservice AS (org.nr 998877001) på 25000 NOK. Opprett en kundefaktura basert på prosjektet.',
        'Create the project "Platform Migration" for customer Coastline Ltd (org no. 987654002) with a budget of 200000 NOK. Log 30 hours for Emma Berg (emma.berg@example.org) at 1500 NOK/h. Register a supplier cost from TechSupply AS (org no. 998877002) of 40000 NOK. Create a customer invoice based on the project.',
    ]

    def extract_expected(self, prompt: str) -> dict:
        result = {}

        # Project name (first quoted string)
        name_match = re.search(r'["\u201c]([^"\u201d]+)["\u201d]', prompt)
        if name_match:
            result["project_name"] = name_match.group(1)

        # Customer org number — first 9-digit number
        org_matches = re.findall(r'\b(\d{9})\b', prompt)
        if org_matches:
            result["customer_org"] = org_matches[0]
        if len(org_matches) >= 2:
            result["supplier_org"] = org_matches[1]

        # Customer name
        cust_match = re.search(
            r'(?:kunden|customer)\s+(.+?)(?:\s*\()', prompt, re.IGNORECASE
        )
        if cust_match:
            result["customer_name"] = cust_match.group(1).strip()

        # Budget
        budget_match = re.search(r'(\d+)\s*NOK', prompt)
        if budget_match:
            result["budget"] = int(budget_match.group(1))

        # Employee email
        emails = re.findall(r'[\w.-]+@[\w.-]+\.\w+', prompt)
        if emails:
            result["employee_email"] = emails[0]

        # Employee name (person before the email)
        emp_match = re.search(
            r'(?:for|für)\s+(\w+\s+\w+)\s*\(', prompt, re.IGNORECASE
        )
        if emp_match:
            result["employee_name"] = emp_match.group(1).strip()

        # Hours
        hours_match = re.search(r'(\d+)\s*(?:timer|hours|Stunden)', prompt, re.IGNORECASE)
        if hours_match:
            result["hours"] = int(hours_match.group(1))

        # Hourly rate
        rate_match = re.search(r'(\d+)\s*(?:NOK/t|NOK/h|NOK/Std)', prompt)
        if rate_match:
            result["hourly_rate"] = int(rate_match.group(1))

        # Supplier name (after "fra" or "from")
        supplier_match = re.search(
            r'(?:fra|from)\s+(.+?)(?:\s*\()', prompt, re.IGNORECASE
        )
        if supplier_match:
            result["supplier_name"] = supplier_match.group(1).strip()

        # Supplier cost amount — the number right before "NOK" that follows the supplier name
        # Look for pattern like "på 25000 NOK" or "of 40000 NOK" after supplier
        cost_match = re.search(
            r'(?:på|of)\s+(\d+)\s*NOK\.\s*(?:Opprett|Create)',
            prompt, re.IGNORECASE,
        )
        if cost_match:
            result["supplier_cost"] = int(cost_match.group(1))
        else:
            # Fallback: last "NUMBER NOK" before the invoice instruction
            all_amounts = re.findall(r'(\d+)\s*NOK', prompt)
            if len(all_amounts) >= 3:
                # budget is first, rate has NOK/t not plain NOK, supplier cost is typically third
                result["supplier_cost"] = int(all_amounts[2])

        return result

    def setup(self, base_url: str, session_token: str, expected: dict):
        """Pre-create customer, employee, and ensure bank account 1920 has bankAccountNumber."""
        customer_org = expected.get("customer_org", "")
        customer_name = expected.get("customer_name", "Test Customer")
        employee_email = expected.get("employee_email", "")
        employee_name = expected.get("employee_name", "Test Employee")
        supplier_org = expected.get("supplier_org", "")
        supplier_name = expected.get("supplier_name", "Test Supplier")

        print(f"  Setting up project lifecycle: customer={customer_name}, employee={employee_name}")

        # 1. Ensure bank account 1920 has bankAccountNumber
        resp = self._api(base_url, session_token, "GET", "/ledger/account", {
            "number": "1920", "fields": "id,version,bankAccountNumber",
        })
        accounts = resp.get("values", [])
        if accounts and not accounts[0].get("bankAccountNumber"):
            self._api(base_url, session_token, "PUT",
                      f"/ledger/account/{accounts[0]['id']}", json_body={
                          "id": accounts[0]["id"],
                          "version": accounts[0]["version"],
                          "bankAccountNumber": "12345678903",
                      })
            print("  Set bankAccountNumber on account 1920")

        # 2. Ensure customer exists
        resp = self._api(base_url, session_token, "GET", "/customer", {
            "organizationNumber": customer_org, "fields": "id", "count": 1,
        })
        if not resp.get("values"):
            self._api(base_url, session_token, "POST", "/customer", json_body={
                "name": customer_name,
                "organizationNumber": customer_org,
                "isCustomer": True,
            })
            print(f"  Created customer: {customer_name}")
        else:
            print(f"  Customer already exists: {customer_name}")

        # 3. Ensure employee exists
        if employee_email:
            resp = self._api(base_url, session_token, "GET", "/employee", {
                "email": employee_email, "fields": "id", "count": 1,
            })
            if not resp.get("values"):
                dept_resp = self._api(base_url, session_token, "GET", "/department", {
                    "fields": "id", "count": 1,
                })
                dept_id = dept_resp.get("values", [{}])[0].get("id")
                parts = employee_name.split(maxsplit=1)
                first_name = parts[0] if parts else "Test"
                last_name = parts[1] if len(parts) > 1 else "Employee"
                resp = self._api(base_url, session_token, "POST", "/employee", json_body={
                    "firstName": first_name,
                    "lastName": last_name,
                    "email": employee_email,
                    "userType": "STANDARD",
                    "department": {"id": dept_id} if dept_id else None,
                })
                emp_id = resp.get("value", {}).get("id")
                print(f"  Created employee: {employee_name} (id={emp_id})")

                # Grant entitlements so they can work on projects
                if emp_id:
                    self._api(base_url, session_token, "PUT",
                              "/employee/entitlement/:grantEntitlementsByTemplate",
                              params={"employeeId": emp_id,
                                      "template": "allTripletexAdministrator"})
                    print(f"  Granted admin entitlements to employee {emp_id}")
            else:
                print(f"  Employee already exists: {employee_email}")

        # 4. Ensure supplier exists (optional — agent should create it, but having it avoids friction)
        if supplier_org:
            resp = self._api(base_url, session_token, "GET", "/supplier", {
                "organizationNumber": supplier_org, "fields": "id", "count": 1,
            })
            if not resp.get("values"):
                self._api(base_url, session_token, "POST", "/supplier", json_body={
                    "name": supplier_name,
                    "organizationNumber": supplier_org,
                })
                print(f"  Created supplier: {supplier_name}")
            else:
                print(f"  Supplier already exists: {supplier_name}")

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        project_name = expected.get("project_name", "")
        customer_org = expected.get("customer_org", "")
        hours = expected.get("hours", 0)
        supplier_cost = expected.get("supplier_cost", 0)

        # --- Check 1: Project exists with correct name (2pts) ---
        resp = verifier.get("/project", {
            "fields": "id,name,customer(*)",
            "count": 50,
        })
        projects = resp.get("values", [])
        project = next(
            (p for p in projects
             if p.get("name", "").lower() == project_name.lower()),
            None,
        )

        checks.append(Check(
            name="Project exists",
            passed=project is not None,
            expected=project_name,
            actual=project.get("name", "NOT FOUND") if project else "NOT FOUND",
            points=2,
        ))

        if not project:
            checks.append(Check(name="Project has customer linked", passed=False, expected="customer", actual="no project", points=1))
            checks.append(Check(name="Timesheet entries logged", passed=False, expected=f"{hours} hours", actual="no project", points=1))
            checks.append(Check(name="Total hours correct", passed=False, expected=f"{hours}h", actual="no project", points=2))
            checks.append(Check(name="Supplier cost voucher", passed=False, expected="voucher", actual="no project", points=2))
            checks.append(Check(name="Customer invoice created", passed=False, expected="invoice", actual="no project", points=2))
            checks.append(Check(name="Invoice amount > 0", passed=False, expected=">0", actual="no project", points=1))
            return checks

        project_id = project["id"]

        # --- Check 2: Project has customer linked (1pt) ---
        customer = project.get("customer", {})
        customer_id = customer.get("id") if customer else None
        checks.append(Check(
            name="Project has customer linked",
            passed=customer_id is not None,
            expected="customer with id",
            actual=f"customer id={customer_id}" if customer_id else "NO customer linked",
            points=1,
        ))

        # --- Check 3: Timesheet entries exist (1pt) ---
        resp = verifier.get("/timesheet/entry", {
            "projectId": project_id,
            "dateFrom": "2020-01-01",
            "dateTo": "2099-12-31",
            "fields": "id,hours,employee(*),activity(*)",
            "count": 50,
        })
        entries = resp.get("values", [])
        total_hours = sum(e.get("hours", 0) for e in entries)

        checks.append(Check(
            name="Timesheet entries logged",
            passed=len(entries) > 0,
            expected=f"at least 1 entry",
            actual=f"{len(entries)} entries ({total_hours}h)",
            points=1,
        ))

        # --- Check 4: Total hours correct (2pts) ---
        checks.append(Check(
            name="Total hours correct",
            passed=hours > 0 and abs(total_hours - hours) < 2,
            expected=f"{hours} hours",
            actual=f"{total_hours} hours",
            points=2,
        ))

        # --- Check 5: Supplier cost voucher exists (2pts) ---
        resp = verifier.get("/ledger/voucher", {
            "dateFrom": "2020-01-01",
            "dateTo": "2099-12-31",
            "count": 30,
            "sorting": "-id",
            "fields": "id,description,postings(*)",
        })
        all_vouchers = resp.get("values", [])
        supplier_voucher_found = False
        for v in all_vouchers:
            posting_accounts = {p.get("account", {}).get("number", 0) for p in v.get("postings", [])}
            # Supplier cost: expense account (4xxx) + accounts payable (2400)
            has_expense = any(4000 <= num <= 4999 for num in posting_accounts)
            has_ap = 2400 in posting_accounts
            if has_expense and has_ap:
                supplier_voucher_found = True
                break

        checks.append(Check(
            name="Supplier cost voucher",
            passed=supplier_voucher_found,
            expected=f"voucher with 4xxx + 2400 (supplier cost ~{supplier_cost})",
            actual="FOUND" if supplier_voucher_found else "NOT FOUND",
            points=2,
        ))

        # --- Check 6 & 7: Customer invoice exists + amount > 0 ---
        invoices = []
        cid = customer_id
        if not cid:
            resp = verifier.get("/customer", {
                "organizationNumber": customer_org, "fields": "id", "count": 1,
            })
            cust_list = resp.get("values", [])
            cid = cust_list[0]["id"] if cust_list else None

        if cid:
            resp = verifier.get("/invoice", {
                "customerId": cid,
                "invoiceDateFrom": "2020-01-01",
                "invoiceDateTo": "2099-12-31",
                "fields": "id,amountExcludingVat,isCreditNote",
                "count": 10,
            })
            invoices = [inv for inv in resp.get("values", []) if not inv.get("isCreditNote")]

        checks.append(Check(
            name="Customer invoice created",
            passed=len(invoices) > 0,
            expected="at least 1 invoice",
            actual=f"{len(invoices)} invoices" if cid else "customer not found",
            points=2,
        ))

        if invoices:
            inv_amount = invoices[0].get("amountExcludingVat", 0)
            checks.append(Check(
                name="Invoice amount > 0",
                passed=inv_amount > 0,
                expected="positive amount",
                actual=f"{inv_amount} NOK",
                points=1,
            ))
        else:
            checks.append(Check(
                name="Invoice amount > 0",
                passed=False,
                expected="positive amount",
                actual="no invoice",
                points=1,
            ))

        return checks
