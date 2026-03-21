"""Task 17: Create accounting dimension with values and post a voucher linked to it."""

import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class DimensionVoucherTask(BaseTask):
    name = "Accounting Dimension + Voucher"
    tier = 2
    optimal_calls = 5  # create dimension + create values + lookup accounts + create voucher

    prompts = [
        'Opprett en fri regnskapsdimensjon "Avdeling" med verdiene "Oslo" og "Bergen". Bokfør deretter et bilag på konto 6300 for 15000 kr, knyttet til dimensjonsverdien "Bergen".',
        'Create a free accounting dimension "Region" with values "North" and "South". Then post a voucher on account 7100 for 22000 NOK linked to the dimension value "South".',
    ]

    def extract_expected(self, prompt: str) -> dict:
        result = {}

        # Extract dimension name (first quoted string)
        quotes = re.findall(r'["\u201c]([^"\u201d]+)["\u201d]', prompt)
        if quotes:
            result["dimension_name"] = quotes[0]
        if len(quotes) >= 3:
            result["value_1"] = quotes[1]
            result["value_2"] = quotes[2]
            result["linked_value"] = quotes[-1]  # Last quoted = the one linked to voucher
        elif len(quotes) >= 2:
            result["value_1"] = quotes[1]
            result["linked_value"] = quotes[-1]

        # Extract account number
        acct_match = re.search(r'konto\s+(\d{4})|account\s+(\d{4})', prompt, re.IGNORECASE)
        if acct_match:
            result["account_number"] = acct_match.group(1) or acct_match.group(2)

        # Extract amount
        amount_match = re.search(r'(\d[\d\s]*\d)\s*(?:kr|NOK)', prompt)
        if amount_match:
            result["amount"] = float(amount_match.group(1).replace(" ", ""))

        return result

    def setup(self, base_url: str, session_token: str, expected: dict):
        """No setup needed — the agent creates everything from scratch."""
        print(f"  Dimension voucher task: no setup needed (agent creates dimension + voucher)")

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        dim_name = expected.get("dimension_name", "")
        linked_value = expected.get("linked_value", "")
        account_number = expected.get("account_number", "")
        amount = expected.get("amount", 0)

        # Check 1: Dimension exists
        # Search for accounting dimension names
        resp = verifier.get("/ledger/accountingDimensionName", {
            "fields": "id,dimensionName,dimensionIndex", "count": 20,
        })
        dimensions = resp.get("values", [])
        dim = next((d for d in dimensions if d.get("dimensionName", "").lower() == dim_name.lower()), None)

        checks.append(Check(
            name=f"Dimension '{dim_name}' created",
            passed=dim is not None,
            expected=dim_name,
            actual=dim.get("dimensionName", "NOT FOUND") if dim else "NOT FOUND",
            points=2,
        ))

        # Check 2: Dimension values exist
        if dim:
            dim_index = dim.get("dimensionIndex")
            resp = verifier.get("/ledger/accountingDimensionValue", {
                "dimensionIndex": dim_index,
                "fields": "id,displayName,dimensionIndex",
                "count": 20,
            })
            values = resp.get("values", [])
            value_names = [v.get("displayName", "") for v in values]

            checks.append(Check(
                name="Dimension values created",
                passed=len(values) >= 2,
                expected="at least 2 values",
                actual=f"{len(values)} values: {value_names}",
                points=2,
            ))

            # Check the linked value exists
            linked_val = next((v for v in values if v.get("displayName", "").lower() == linked_value.lower()), None)
            checks.append(Check(
                name=f"Value '{linked_value}' exists",
                passed=linked_val is not None,
                expected=linked_value,
                actual="FOUND" if linked_val else "NOT FOUND",
            ))

        # Check 3: Voucher with dimension value linked
        acct_resp = verifier.get("/ledger/account", {
            "number": account_number, "fields": "id", "count": 1,
        })
        acct_id = acct_resp.get("values", [{}])[0].get("id") if acct_resp.get("values") else None

        if acct_id:
            voucher_resp = verifier.get("/ledger/voucher", {
                "dateFrom": "2026-01-01", "dateTo": "2099-12-31",
                "count": 20, "sorting": "-number",
            })
            has_voucher = False
            for v in voucher_resp.get("values", [])[:10]:
                v_detail = verifier.get(f"/ledger/voucher/{v['id']}", {"fields": "id,postings(*)"})
                for p in v_detail.get("value", {}).get("postings", []):
                    if (p.get("account", {}).get("id") == acct_id
                            and p.get("amountGross", 0) > 0
                            and abs(p.get("amountGross", 0) - amount) < 100):
                        has_voucher = True
                        break
                if has_voucher:
                    break

            checks.append(Check(
                name=f"Voucher on account {account_number} for {amount} NOK",
                passed=has_voucher,
                expected=f"posting on {account_number} for ~{amount}",
                actual="FOUND" if has_voucher else "NOT FOUND",
                points=3,
            ))

        return checks
