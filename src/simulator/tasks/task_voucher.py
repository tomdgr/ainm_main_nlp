"""Task: Post expense voucher to correct account and department."""

import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class VoucherExpenseTask(BaseTask):
    """Tier 2 task: Create a voucher posting an expense to the correct account and department.

    Simulates task_22 (receipt/expense posting) and task_17 (voucher with dimensions).
    """

    name = "Post Expense Voucher"
    tier = 2
    optimal_calls = 4  # dept lookup + account lookup + vatType + POST voucher

    prompts = [
        "Bokfør en utgift på 4500 kr inkludert MVA for Kontorrekvisita til avdeling Økonomi. Bruk konto 6300 og korrekt MVA-behandling.",
        "Registrer et kjøp av Drivstoff på 1850 kr inkludert MVA for avdeling Logistikk. Bruk konto 7000 og standard MVA.",
        "Vi har mottatt en kvittering for Renhold på 3200 kr inkludert MVA. Bokfør til avdeling Drift på konto 6340.",
    ]

    def extract_expected(self, prompt: str) -> dict:
        result = {}

        # Extract amount
        amount_match = re.search(r'(\d[\d\s]*\d)\s*(?:kr|NOK)', prompt)
        if amount_match:
            result["amount"] = float(amount_match.group(1).replace(" ", ""))

        # Extract department name
        dept_match = re.search(r'avdeling\s+(\w+)', prompt, re.IGNORECASE)
        if dept_match:
            result["department"] = dept_match.group(1)

        # Extract account number
        acct_match = re.search(r'konto\s+(\d{4})', prompt, re.IGNORECASE)
        if acct_match:
            result["account_number"] = int(acct_match.group(1))

        # Extract description
        for pattern in [r'for\s+(\w+)', r'av\s+(\w+)', r'for\s+(\w+\s+\w+)']:
            m = re.search(pattern, prompt)
            if m:
                result["description"] = m.group(1)
                break

        return result

    def setup(self, base_url: str, session_token: str, expected: dict):
        """Ensure the department exists."""
        dept_name = expected.get("department", "")
        if not dept_name:
            return

        resp = self._api(base_url, session_token, "GET", "/department", {
            "name": dept_name, "fields": "id,name", "count": 1,
        })
        depts = resp.get("values", [])
        if depts:
            print(f"  Department '{dept_name}' exists: id={depts[0]['id']}")
        else:
            resp = self._api(base_url, session_token, "POST", "/department", json_body={
                "name": dept_name,
            })
            did = resp.get("value", {}).get("id")
            print(f"  Created department '{dept_name}': id={did}")

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        account_number = expected.get("account_number", 0)
        amount = expected.get("amount", 0)
        dept_name = expected.get("department", "")

        # Find the account ID for the expected account number
        acct_resp = verifier.get("/ledger/account", {
            "number": str(account_number), "fields": "id,number,name", "count": 1,
        })
        acct_id = None
        acct_values = acct_resp.get("values", [])
        if acct_values:
            acct_id = acct_values[0].get("id")

        # Find recent vouchers and check postings (need explicit field expansion)
        resp = verifier.get("/ledger/voucher", {
            "dateFrom": "2026-03-01",
            "dateTo": "2099-12-31",
            "count": 10,
            "sorting": "-number",
        })
        vouchers = resp.get("values", [])

        matching = []
        for v in vouchers[:5]:
            v_detail = verifier.get(f"/ledger/voucher/{v['id']}", {
                "fields": "id,description,postings(*)",
            })
            v_data = v_detail.get("value", {})
            for p in v_data.get("postings", []):
                p_acct_id = p.get("account", {}).get("id")
                p_amount = p.get("amountGross") or 0
                if p_acct_id == acct_id and p_amount > 0 and abs(p_amount - amount) < 10:
                    matching.append(p)
                    break
            if matching:
                break

        checks.append(Check(
            name="Expense posting found",
            passed=len(matching) > 0,
            expected=f"posting on account {account_number} for ~{amount}",
            actual=f"found {len(matching)} matching postings",
            points=3,
        ))

        if not matching:
            return checks

        posting = matching[-1]  # Take the most recent

        # Check amount
        checks.append(Check(
            name="Amount correct",
            passed=abs(posting.get("amountGross", 0) - amount) < 10,
            expected=str(amount),
            actual=str(posting.get("amountGross", 0)),
            points=2,
        ))

        # Check department if applicable
        posting_dept = posting.get("department")
        if dept_name and posting_dept:
            # Need to look up the department name
            dept_id = posting_dept.get("id")
            if dept_id:
                dept_resp = verifier.get(f"/department/{dept_id}", {"fields": "id,name"})
                dept_val = dept_resp.get("value", {})
                actual_dept = dept_val.get("name", "")
                checks.append(Check(
                    name="Department correct",
                    passed=actual_dept.lower() == dept_name.lower(),
                    expected=dept_name,
                    actual=actual_dept or "NONE",
                    points=2,
                ))
        elif dept_name:
            checks.append(Check(
                name="Department linked",
                passed=False,
                expected=dept_name,
                actual="No department on posting",
                points=2,
            ))

        # Check that VAT was applied (posting should have a vatType)
        vat_type = posting.get("vatType", {})
        checks.append(Check(
            name="VAT type set",
            passed=vat_type.get("id", 0) > 0,
            expected="VAT type assigned",
            actual=f"vatType id={vat_type.get('id', 0)}",
        ))

        return checks
