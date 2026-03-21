"""Task 30: Simplified year-end closing with depreciation + tax provision."""

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask

# Revenue and expense vouchers to seed so the agent has P&L data
_REVENUE_VOUCHERS = [
    ("Salgsinntekter Q1", 3000, 1920, 500000.0, "2025-03-31"),
    ("Salgsinntekter Q2", 3000, 1920, 450000.0, "2025-06-30"),
    ("Salgsinntekter Q3", 3000, 1920, 480000.0, "2025-09-30"),
    ("Salgsinntekter Q4", 3000, 1920, 520000.0, "2025-12-31"),
]

_EXPENSE_VOUCHERS = [
    ("Loennskostnad 2025", 5000, 1920, 600000.0, "2025-06-30"),
    ("Kontorkostnader 2025", 6300, 1920, 120000.0, "2025-06-30"),
    ("Reisekostnader 2025", 7100, 1920, 80000.0, "2025-09-30"),
]

# Asset purchase vouchers (these are the assets the agent must depreciate)
_ASSET_PURCHASES = [
    ("Kjop IT-utstyr", 1200, 1920, 300000.0, "2025-01-02"),
    ("Kjop Programvare", 1210, 1920, 150000.0, "2025-01-02"),
]


class YearEndTask(BaseTask):
    """Tier 3 task: Simplified year-end closing with depreciation + tax provision.

    Seeds revenue/expense/asset vouchers for 2025, then asks the agent to:
    1. Depreciate 2 assets (straight-line)
    2. Reverse a prepaid expense
    3. Calculate and book tax provision at 22%

    Checks are lenient -- we just verify vouchers were created, not exact amounts,
    because the shared sandbox ledger state varies.
    """

    name = "Year-End Closing"
    tier = 3
    optimal_calls = 10  # account lookups + create missing accounts + depreciation vouchers + balanceSheet + tax voucher

    prompts = [
        "Gjennomfor forenklet arsavslutning for 2025. Avskriv folgende eiendeler lineaert: IT-utstyr 300000 kr over 5 ar (konto 1200), Programvare 150000 kr over 3 ar (konto 1210). Reverser forskuddsbetalt husleie 60000 kr (konto 1700, manedlig 5000 kr til konto 6300). Beregn skattekostnad med 22% sats.",
        "Perform simplified year-end closing for 2025. Depreciate the following assets using straight-line method: IT equipment NOK 300,000 over 5 years (account 1200), Software NOK 150,000 over 3 years (account 1210). Reverse prepaid rent NOK 60,000 (account 1700, monthly NOK 5,000 to account 6300). Calculate tax expense at 22% rate.",
    ]

    def __init__(self, task_id: str):
        super().__init__(task_id)
        self._account_ids: dict[int, int] = {}  # account_number -> account_id

    def extract_expected(self, prompt: str) -> dict:
        return {
            "assets": [
                {"name": "IT-utstyr", "cost": 300000, "life_years": 5, "account": 1200, "annual_depr": 60000},
                {"name": "Programvare", "cost": 150000, "life_years": 3, "account": 1210, "annual_depr": 50000},
            ],
            "prepaid_amount": 60000,
            "tax_rate": 0.22,
        }

    def _lookup_account(self, base_url: str, session_token: str, acct_num: int) -> int | None:
        """Look up account by number and cache the id."""
        if acct_num in self._account_ids:
            return self._account_ids[acct_num]
        resp = self._api(base_url, session_token, "GET", "/ledger/account", {
            "number": str(acct_num), "fields": "id,number,name", "count": 1,
        })
        vals = resp.get("values", [])
        if vals:
            self._account_ids[acct_num] = vals[0]["id"]
            print(f"  Account {acct_num}: id={vals[0]['id']}, name={vals[0].get('name')}")
            return vals[0]["id"]
        return None

    def _ensure_account(self, base_url: str, session_token: str, acct_num: int, name: str) -> int | None:
        """Look up an account; create it if missing."""
        acct_id = self._lookup_account(base_url, session_token, acct_num)
        if acct_id:
            return acct_id
        # Create the missing account
        resp = self._api(base_url, session_token, "POST", "/ledger/account", json_body={
            "number": acct_num,
            "name": name,
        })
        val = resp.get("value", {})
        if val.get("id"):
            self._account_ids[acct_num] = val["id"]
            print(f"  Created account {acct_num}: id={val['id']}, name={name}")
            return val["id"]
        print(f"  WARNING: Could not create account {acct_num}")
        return None

    def setup(self, base_url: str, session_token: str, expected: dict):
        """Seed revenue, expense, and asset purchase vouchers for 2025."""

        # Ensure all needed accounts exist
        needed_accounts = {
            3000: "Salgsinntekter",
            5000: "Lonn",
            6300: "Leie lokale",
            7100: "Bilgodtgjorelse",
            1200: "Maskiner og anlegg",
            1210: "Programvare",
            1920: "Bankinnskudd",
            1700: "Forskuddsbetalt leiekostnad",
            6010: "Avskrivning",
            1209: "Akkumulerte avskrivninger",
            8700: "Skattekostnad",
            2920: "Betalbar skatt",
        }

        for acct_num, name in needed_accounts.items():
            self._ensure_account(base_url, session_token, acct_num, name)

        # Ensure bank account 1920 has a bank account number (required for some vouchers)
        bank_id = self._account_ids.get(1920)
        if bank_id:
            resp = self._api(base_url, session_token, "GET", f"/ledger/account/{bank_id}", {
                "fields": "id,version,bankAccountNumber",
            })
            val = resp.get("value", resp)
            if val.get("id") and not val.get("bankAccountNumber"):
                self._api(base_url, session_token, "PUT", f"/ledger/account/{bank_id}", json_body={
                    "id": bank_id,
                    "version": val["version"],
                    "bankAccountNumber": "12345678903",
                })

        # Post revenue vouchers (account 3000 requires vatType)
        for desc, rev_acct, bank_acct, amount, date in _REVENUE_VOUCHERS:
            rev_id = self._account_ids.get(rev_acct)
            bank_id = self._account_ids.get(bank_acct)
            if not rev_id or not bank_id:
                continue
            self._api(base_url, session_token, "POST", "/ledger/voucher",
                      params={"sendToLedger": "true"}, json_body={
                "date": date,
                "description": desc,
                "postings": [
                    {"row": 1, "account": {"id": bank_id}, "amountGross": amount, "amountGrossCurrency": amount},
                    {"row": 2, "account": {"id": rev_id}, "amountGross": -amount, "amountGrossCurrency": -amount, "vatType": {"id": 3}},
                ],
            })
            print(f"  Revenue voucher: {desc} = {amount}")

        # Post expense vouchers
        for desc, exp_acct, bank_acct, amount, date in _EXPENSE_VOUCHERS:
            exp_id = self._account_ids.get(exp_acct)
            bank_id = self._account_ids.get(bank_acct)
            if not exp_id or not bank_id:
                continue
            self._api(base_url, session_token, "POST", "/ledger/voucher",
                      params={"sendToLedger": "true"}, json_body={
                "date": date,
                "description": desc,
                "postings": [
                    {"row": 1, "account": {"id": exp_id}, "amountGross": amount, "amountGrossCurrency": amount},
                    {"row": 2, "account": {"id": bank_id}, "amountGross": -amount, "amountGrossCurrency": -amount},
                ],
            })
            print(f"  Expense voucher: {desc} = {amount}")

        # Post asset purchase vouchers
        for desc, asset_acct, bank_acct, amount, date in _ASSET_PURCHASES:
            asset_id = self._account_ids.get(asset_acct)
            bank_id = self._account_ids.get(bank_acct)
            if not asset_id or not bank_id:
                continue
            self._api(base_url, session_token, "POST", "/ledger/voucher",
                      params={"sendToLedger": "true"}, json_body={
                "date": date,
                "description": desc,
                "postings": [
                    {"row": 1, "account": {"id": asset_id}, "amountGross": amount, "amountGrossCurrency": amount},
                    {"row": 2, "account": {"id": bank_id}, "amountGross": -amount, "amountGrossCurrency": -amount},
                ],
            })
            print(f"  Asset voucher: {desc} = {amount}")

        # Post prepaid rent voucher (1700 debit, 1920 credit)
        prepaid_id = self._account_ids.get(1700)
        bank_id = self._account_ids.get(1920)
        if prepaid_id and bank_id:
            self._api(base_url, session_token, "POST", "/ledger/voucher",
                      params={"sendToLedger": "true"}, json_body={
                "date": "2025-01-02",
                "description": "Forskuddsbetalt husleie 2025",
                "postings": [
                    {"row": 1, "account": {"id": prepaid_id}, "amountGross": 60000.0, "amountGrossCurrency": 60000.0},
                    {"row": 2, "account": {"id": bank_id}, "amountGross": -60000.0, "amountGrossCurrency": -60000.0},
                ],
            })
            print("  Prepaid rent voucher: 60000")

    def check(self, verifier, expected: dict) -> list[Check]:
        """Verify year-end vouchers were created with correct amounts and accounts."""
        checks = []
        assets = expected.get("assets", [])

        # Fetch recent vouchers (sorted by most recent first)
        # Note: dateTo is exclusive in Tripletex, so use 2026-01-01 to include 2025-12-31
        resp = verifier.get("/ledger/voucher", {
            "dateFrom": "2025-12-01",
            "dateTo": "2026-01-01",
            "count": 50,
            "sorting": "-id",
            "fields": "id,date,description,postings(account(number),amountGross)",
        })
        vouchers = resp.get("values", [])

        # ---- Classify vouchers ----
        depreciation_vouchers = []
        tax_vouchers = []
        prepaid_vouchers = []

        for v in vouchers:
            postings = v.get("postings", [])
            desc = (v.get("description") or "").lower()
            posting_accounts = {p.get("account", {}).get("number", 0) for p in postings}

            is_depr = "avskriv" in desc or "depreci" in desc or bool(posting_accounts & {6010, 1209})
            is_tax = "skatt" in desc or "tax" in desc or bool(posting_accounts & {8700, 2920})
            is_prepaid = "forskudd" in desc or "prepaid" in desc or "opplosning" in desc or 1700 in posting_accounts

            if is_depr:
                depreciation_vouchers.append(v)
            if is_tax:
                tax_vouchers.append(v)
            if is_prepaid:
                prepaid_vouchers.append(v)

        # ---- Check 1: Depreciation vouchers exist (2pts) ----
        checks.append(Check(
            name="Depreciation vouchers created",
            passed=len(depreciation_vouchers) >= 2,
            expected="at least 2 depreciation vouchers",
            actual=f"{len(depreciation_vouchers)} depreciation vouchers found",
            points=2,
        ))

        # ---- Check 2 & 3: Depreciation amounts correct (2pts each) ----
        # Collect all debit amounts on depreciation expense accounts (6000-6099 range)
        depr_amounts = []
        for v in depreciation_vouchers:
            for p in v.get("postings", []):
                acct_num = p.get("account", {}).get("number", 0)
                amount = abs(p.get("amountGross", 0))
                if 6000 <= acct_num <= 6099 and amount > 0:
                    depr_amounts.append(amount)

        for asset in assets:
            expected_depr = asset["annual_depr"]
            name = asset["name"]
            tolerance = expected_depr * 0.10  # ±10%
            found = any(abs(a - expected_depr) <= tolerance for a in depr_amounts)
            checks.append(Check(
                name=f"{name} depreciation amount correct ({expected_depr})",
                passed=found,
                expected=f"~{expected_depr} on account 60xx",
                actual=f"60xx amounts: {depr_amounts}" if depr_amounts else "no 60xx postings",
                points=2,
            ))

        # ---- Check 4: Depreciation uses correct account pair: expense (60xx) + accumulated (12xx) ----
        has_correct_pair = False
        for v in depreciation_vouchers:
            posting_nums = {p.get("account", {}).get("number", 0) for p in v.get("postings", [])}
            has_expense = any(6000 <= n <= 6099 for n in posting_nums)
            has_accum = any(1200 <= n <= 1299 for n in posting_nums)
            if has_expense and has_accum:
                has_correct_pair = True
                break
        checks.append(Check(
            name="Depreciation uses correct accounts (60xx/12xx)",
            passed=has_correct_pair,
            expected="60xx (expense) + 12xx (accumulated)",
            actual="correct pair found" if has_correct_pair else f"pairs: {[{p.get('account',{}).get('number',0) for p in v.get('postings',[])} for v in depreciation_vouchers[:3]]}",
            points=1,
        ))

        # ---- Check 5: Prepaid reversal voucher exists (1pt) ----
        checks.append(Check(
            name="Prepaid reversal voucher",
            passed=len(prepaid_vouchers) >= 1,
            expected="at least 1 prepaid reversal voucher (account 1700)",
            actual=f"{len(prepaid_vouchers)} prepaid vouchers found",
            points=1,
        ))

        # ---- Check 6: Tax provision voucher exists (2pts) ----
        checks.append(Check(
            name="Tax provision voucher created",
            passed=len(tax_vouchers) >= 1,
            expected="at least 1 tax provision voucher",
            actual=f"{len(tax_vouchers)} tax provision vouchers found",
            points=2,
        ))

        # ---- Check 7: Tax provision uses correct account pair: expense (87xx) + payable (25xx-29xx) ----
        has_tax_pair = False
        for v in tax_vouchers:
            posting_nums = {p.get("account", {}).get("number", 0) for p in v.get("postings", [])}
            has_tax_expense = any(8700 <= n <= 8799 for n in posting_nums)
            has_tax_payable = any(2500 <= n <= 2999 for n in posting_nums)
            if has_tax_expense and has_tax_payable:
                has_tax_pair = True
                break
        checks.append(Check(
            name="Tax provision uses correct accounts (87xx/2xxx)",
            passed=has_tax_pair,
            expected="87xx (expense) + 25xx-29xx (payable)",
            actual="correct pair found" if has_tax_pair else "pair not found",
            points=1,
        ))

        # ---- Check 8: Total year-end vouchers >= 3 (2pts) ----
        year_end_ids = set()
        for v in depreciation_vouchers + tax_vouchers + prepaid_vouchers:
            year_end_ids.add(v["id"])

        checks.append(Check(
            name="Total year-end vouchers",
            passed=len(year_end_ids) >= 3,
            expected="at least 3 year-end adjustment vouchers",
            actual=f"{len(year_end_ids)} year-end vouchers found",
            points=2,
        ))

        return checks
