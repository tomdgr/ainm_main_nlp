"""Task: Find and correct ledger errors with correction vouchers."""

import datetime
import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class LedgerCorrectionTask(BaseTask):
    """Tier 3 task: Find 4 specific ledger errors and create correction vouchers.

    Setup creates 4 vouchers with deliberate errors:
    1. Wrong account (posted to account A instead of B)
    2. Duplicate voucher (same posting appears twice)
    3. Missing VAT line (expense without VAT that should have it)
    4. Wrong amount (posted X instead of Y)

    The agent must find these errors and create correction vouchers.
    """

    name = "Correct Ledger Errors"
    tier = 3
    optimal_calls = 10  # ~3 GETs to find errors + ~4 correction vouchers + lookups

    # Error configuration for setup
    _ERROR_CONFIG = {
        "wrong_acct": "6300",       # Wrong: posted to 6300
        "right_acct": "7100",       # Should have been 7100
        "wrong_acct_amount": 2350,
        "dup_acct": "6540",         # Duplicate posting on this account
        "dup_amount": 1300,
        "vat_acct": "7300",         # Missing VAT on this expense
        "vat_amount": 5550,         # Amount excl VAT
        "bad_amt_acct": "6590",     # Wrong amount on this account
        "bad_amt_posted": 16700,    # What was posted
        "bad_amt_correct": 6350,    # What should have been posted
    }

    _PROMPT_TEMPLATE = (
        "Vi har oppdaget feil i hovedboken for januar og februar 2026. "
        "Gå gjennom alle bilag og finn de 4 feilene: "
        "en postering på feil konto (konto {wrong_acct} brukt i stedet for {right_acct}, beløp {wrong_acct_amount} kr), "
        "et duplisert bilag (konto {dup_acct}, beløp {dup_amount} kr), "
        "en manglende MVA-linje (konto {vat_acct}, beløp ekskl. {vat_amount} kr mangler MVA på konto 2710), "
        "og et feil beløp (konto {bad_amt_acct}, {bad_amt_posted} kr bokført i stedet for {bad_amt_correct} kr). "
        "Korriger alle feil med riktige bilag."
    )

    def extract_expected(self, prompt: str) -> dict:
        """Extract the error details from the prompt."""
        result = dict(self._ERROR_CONFIG)

        # Try to parse actual values from prompt (they may differ per variant)
        # Wrong account
        m = re.search(r'konto\s+(\d{4})\s+brukt\s+i\s+stedet\s+for\s+(\d{4}).*?(\d+)\s*kr', prompt)
        if m:
            result["wrong_acct"] = m.group(1)
            result["right_acct"] = m.group(2)
            result["wrong_acct_amount"] = int(m.group(3))

        # Duplicate
        m = re.search(r'duplisert.*?konto\s+(\d{4}).*?(\d+)\s*kr', prompt)
        if m:
            result["dup_acct"] = m.group(1)
            result["dup_amount"] = int(m.group(2))

        # Missing VAT
        m = re.search(r'manglende\s+MVA.*?konto\s+(\d{4}).*?(\d+)\s*kr', prompt)
        if m:
            result["vat_acct"] = m.group(1)
            result["vat_amount"] = int(m.group(2))

        # Wrong amount
        m = re.search(r'feil\s+beløp.*?konto\s+(\d{4}).*?(\d+)\s*kr\s+bokført\s+i\s+stedet\s+for\s+(\d+)', prompt)
        if m:
            result["bad_amt_acct"] = m.group(1)
            result["bad_amt_posted"] = int(m.group(2))
            result["bad_amt_correct"] = int(m.group(3))

        return result

    @property
    def prompts(self) -> list[str]:
        """Generate prompts from template with error config values."""
        return [self._PROMPT_TEMPLATE.format(**self._ERROR_CONFIG)]

    def _find_account_id(self, base_url: str, session_token: str, number: str) -> int | None:
        """Look up a ledger account by number."""
        resp = self._api(base_url, session_token, "GET", "/ledger/account", {
            "number": number, "fields": "id,number", "count": 1,
        })
        values = resp.get("values", [])
        return values[0]["id"] if values else None

    def _find_bank_account_id(self, base_url: str, session_token: str) -> int | None:
        """Get account 1920 (bank) ID."""
        return self._find_account_id(base_url, session_token, "1920")

    def setup(self, base_url: str, session_token: str, expected: dict):
        """Create 4 vouchers with deliberate errors for the agent to find and correct."""
        today = datetime.date.today().isoformat()
        jan_date = "2026-01-15"
        feb_date = "2026-02-10"

        print(f"  Setting up ledger correction task with 4 deliberate errors")

        bank_id = self._find_bank_account_id(base_url, session_token)
        if not bank_id:
            print("  ERROR: Bank account 1920 not found")
            return

        # --- Error 1: Wrong account (posted to wrong_acct instead of right_acct) ---
        wrong_acct_id = self._find_account_id(base_url, session_token, expected["wrong_acct"])
        if wrong_acct_id:
            amount = expected["wrong_acct_amount"]
            resp = self._api(base_url, session_token, "POST", "/ledger/voucher",
                params={"sendToLedger": True},
                json_body={
                    "date": jan_date,
                    "description": f"Expense posted to wrong account",
                    "postings": [
                        {"row": 1, "date": jan_date, "account": {"id": wrong_acct_id},
                         "amountGross": amount, "amountGrossCurrency": amount},
                        {"row": 2, "date": jan_date, "account": {"id": bank_id},
                         "amountGross": -amount, "amountGrossCurrency": -amount},
                    ],
                })
            vid = resp.get("value", {}).get("id")
            print(f"  Error 1 (wrong account {expected['wrong_acct']}): voucher {vid}")
            expected["_wrong_acct_voucher_id"] = vid

        # --- Error 2: Duplicate voucher ---
        dup_acct_id = self._find_account_id(base_url, session_token, expected["dup_acct"])
        if dup_acct_id:
            amount = expected["dup_amount"]
            for i in range(2):  # Create the same voucher twice
                resp = self._api(base_url, session_token, "POST", "/ledger/voucher",
                    params={"sendToLedger": True},
                    json_body={
                        "date": jan_date,
                        "description": f"Office supplies purchase",
                        "postings": [
                            {"row": 1, "date": jan_date, "account": {"id": dup_acct_id},
                             "amountGross": amount, "amountGrossCurrency": amount},
                            {"row": 2, "date": jan_date, "account": {"id": bank_id},
                             "amountGross": -amount, "amountGrossCurrency": -amount},
                        ],
                    })
                vid = resp.get("value", {}).get("id")
                print(f"  Error 2 (duplicate {i+1}/2, acct {expected['dup_acct']}): voucher {vid}")
                if i == 1:
                    expected["_dup_voucher_id"] = vid  # The duplicate to reverse

        # --- Error 3: Missing VAT line ---
        vat_acct_id = self._find_account_id(base_url, session_token, expected["vat_acct"])
        if vat_acct_id:
            amount = expected["vat_amount"]
            # Post without VAT (the error — should have had 25% VAT)
            resp = self._api(base_url, session_token, "POST", "/ledger/voucher",
                params={"sendToLedger": True},
                json_body={
                    "date": feb_date,
                    "description": f"Service invoice without VAT",
                    "postings": [
                        {"row": 1, "date": feb_date, "account": {"id": vat_acct_id},
                         "amountGross": amount, "amountGrossCurrency": amount},
                        {"row": 2, "date": feb_date, "account": {"id": bank_id},
                         "amountGross": -amount, "amountGrossCurrency": -amount},
                    ],
                })
            vid = resp.get("value", {}).get("id")
            print(f"  Error 3 (missing VAT, acct {expected['vat_acct']}): voucher {vid}")

        # --- Error 4: Wrong amount ---
        bad_amt_acct_id = self._find_account_id(base_url, session_token, expected["bad_amt_acct"])
        if bad_amt_acct_id:
            wrong_amount = expected["bad_amt_posted"]
            resp = self._api(base_url, session_token, "POST", "/ledger/voucher",
                params={"sendToLedger": True},
                json_body={
                    "date": feb_date,
                    "description": f"Consulting fee (wrong amount)",
                    "postings": [
                        {"row": 1, "date": feb_date, "account": {"id": bad_amt_acct_id},
                         "amountGross": wrong_amount, "amountGrossCurrency": wrong_amount},
                        {"row": 2, "date": feb_date, "account": {"id": bank_id},
                         "amountGross": -wrong_amount, "amountGrossCurrency": -wrong_amount},
                    ],
                })
            vid = resp.get("value", {}).get("id")
            print(f"  Error 4 (wrong amount {wrong_amount} on acct {expected['bad_amt_acct']}): voucher {vid}")

        print(f"  Setup complete — 4 error vouchers created")

    def check(self, verifier, expected: dict) -> list[Check]:
        """Verify the agent created correction vouchers for each error."""
        checks = []

        # Get all recent vouchers (including corrections the agent created)
        resp = verifier.get("/ledger/voucher", {
            "dateFrom": "2026-01-01",
            "dateTo": "2099-12-31",
            "count": 100,
        })
        all_vouchers = resp.get("values", [])

        # Count vouchers — the agent should have created correction vouchers
        # Setup creates ~5 vouchers (4 errors + 1 duplicate = 5)
        # Agent should create at least 4 correction vouchers
        correction_count = max(0, len(all_vouchers) - 5)

        checks.append(Check(
            name="Correction vouchers created",
            passed=correction_count >= 3,
            expected="at least 3 correction vouchers",
            actual=f"{correction_count} new vouchers (total {len(all_vouchers)})",
            points=3,
        ))

        # Check if the right account now has postings (error 1 correction)
        right_acct_id = None
        acct_resp = verifier.get("/ledger/account", {
            "number": expected["right_acct"], "fields": "id", "count": 1,
        })
        if acct_resp.get("values"):
            right_acct_id = acct_resp["values"][0]["id"]

        if right_acct_id:
            posting_resp = verifier.get("/ledger/posting", {
                "dateFrom": "2026-01-01",
                "dateTo": "2099-12-31",
                "count": 100,
            })
            postings = posting_resp.get("values", [])
            has_right_acct = any(
                p.get("account", {}).get("id") == right_acct_id
                and p.get("amountGross", 0) > 0
                for p in postings
            )
            checks.append(Check(
                name=f"Correction: posting moved to account {expected['right_acct']}",
                passed=has_right_acct,
                expected=f"posting on account {expected['right_acct']}",
                actual="FOUND" if has_right_acct else "NOT FOUND",
                points=2,
            ))

        # Check for reversed duplicate (error 2)
        dup_voucher_id = expected.get("_dup_voucher_id")
        if dup_voucher_id:
            dup_resp = verifier.get(f"/ledger/voucher/{dup_voucher_id}", {})
            dup_voucher = dup_resp.get("value", {})
            # Check if it was reversed (reverseVoucher field set)
            is_reversed = dup_voucher.get("reverseVoucher") is not None
            checks.append(Check(
                name="Duplicate voucher reversed",
                passed=is_reversed,
                expected="reverseVoucher set",
                actual="reversed" if is_reversed else "not reversed",
                points=2,
            ))

        # Check for VAT correction (error 3) — look for posting on account 2710
        vat_acct_id = None
        vat_resp = verifier.get("/ledger/account", {
            "number": "2710", "fields": "id", "count": 1,
        })
        if vat_resp.get("values"):
            vat_acct_id = vat_resp["values"][0]["id"]

        if vat_acct_id:
            posting_resp = verifier.get("/ledger/posting", {
                "dateFrom": "2026-01-01",
                "dateTo": "2099-12-31",
                "count": 200,
            })
            postings = posting_resp.get("values", [])
            has_vat_posting = any(
                p.get("account", {}).get("id") == vat_acct_id
                for p in postings
            )
            checks.append(Check(
                name="VAT correction: posting on account 2710",
                passed=has_vat_posting,
                expected="VAT posting on 2710",
                actual="FOUND" if has_vat_posting else "NOT FOUND",
                points=2,
            ))

        return checks
