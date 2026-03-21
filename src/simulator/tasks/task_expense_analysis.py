"""Task 28: Analyze expense increase between months and create internal projects with activities."""

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask

# Pre-defined expense postings to seed in the sandbox
_JAN_EXPENSES = [
    ("Lønn januar", 5000, 40000.0),
    ("Kontorrekvisita jan", 6300, 3500.0),
    ("Bilgodtgjørelse jan", 7100, 2000.0),
    ("IT-utstyr jan", 6500, 1500.0),
]

_FEB_EXPENSES = [
    ("Lønn februar", 5000, 42000.0),
    ("Kontorrekvisita feb", 6300, 8500.0),  # +5000 increase
    ("Bilgodtgjørelse feb", 7100, 9000.0),  # +7000 increase
    ("IT-utstyr feb", 6500, 7500.0),        # +6000 increase
]

# Expected top 3 increases: 7100 (+7000), 6500 (+6000), 6300 (+5000)


class ExpenseAnalysisTask(BaseTask):
    """Tier 3 task: Analyze ledger expense increases, create projects with activities.

    Seeds Jan/Feb expense vouchers, then asks agent to find top 3 increases
    and create internal projects with unique activities for each.
    """

    name = "Expense Analysis + Projects"
    tier = 3
    optimal_calls = 12  # whoAmI + 2×postingByDate + 3×account + 3×activity + 3×project

    prompts = [
        "De totale kostnadene har økt betydelig fra januar til februar 2026. Analyser hovedboken og identifiser de tre utgiftskontoene med størst økning. Opprett et internt prosjekt for hver av de tre kontoene med kontonavnet. Opprett også en aktivitet for hvert prosjekt.",
        "Total costs have increased significantly from January to February 2026. Analyze the general ledger and identify the three expense accounts with the largest increase. Create an internal project for each of the three accounts with the account name. Also create an activity for each project.",
    ]

    def __init__(self, task_id: str):
        super().__init__(task_id)
        self._account_ids: dict[int, int] = {}  # account_number → account_id
        self._account_names: dict[int, str] = {}  # account_number → account_name

    def extract_expected(self, prompt: str) -> dict:
        return {
            "top_3_accounts": [7100, 6500, 6300],  # Expected order by increase
        }

    def setup(self, base_url: str, session_token: str, expected: dict):
        """Clean up previous run artifacts, then seed expense vouchers."""
        # Note: sandbox accumulates activities from previous runs (can't delete — 403).
        # The agent handles name collisions by appending suffixes.
        # On competition's fresh sandbox, no collisions occur.

        # Look up expense accounts
        for _, acct_num, _ in _JAN_EXPENSES:
            if acct_num in self._account_ids:
                continue
            resp = self._api(base_url, session_token, "GET", "/ledger/account", {
                "number": str(acct_num), "fields": "id,number,name", "count": 1,
            })
            vals = resp.get("values", [])
            if vals:
                self._account_ids[acct_num] = vals[0]["id"]
                self._account_names[acct_num] = vals[0].get("name", "")
                print(f"  Account {acct_num}: id={vals[0]['id']}, name={vals[0].get('name')}")

        # Look up bank account 1920
        resp = self._api(base_url, session_token, "GET", "/ledger/account", {
            "number": "1920", "fields": "id,version,bankAccountNumber", "count": 1,
        })
        bank_acct = resp.get("values", [{}])[0]
        bank_id = bank_acct.get("id")
        if bank_id and not bank_acct.get("bankAccountNumber"):
            self._api(base_url, session_token, "PUT", f"/ledger/account/{bank_id}", json_body={
                "id": bank_id, "version": bank_acct["version"],
                "bankAccountNumber": "12345678903",
            })

        # Post January vouchers
        for desc, acct_num, amount in _JAN_EXPENSES:
            acct_id = self._account_ids.get(acct_num)
            if not acct_id:
                continue
            self._api(base_url, session_token, "POST", "/ledger/voucher",
                      params={"sendToLedger": "true"}, json_body={
                "date": "2026-01-15",
                "description": desc,
                "postings": [
                    {"row": 1, "account": {"id": acct_id}, "amountGross": amount, "amountGrossCurrency": amount},
                    {"row": 2, "account": {"id": bank_id}, "amountGross": -amount, "amountGrossCurrency": -amount},
                ],
            })
            print(f"  Jan voucher: {desc} = {amount}")

        # Post February vouchers
        for desc, acct_num, amount in _FEB_EXPENSES:
            acct_id = self._account_ids.get(acct_num)
            if not acct_id:
                continue
            self._api(base_url, session_token, "POST", "/ledger/voucher",
                      params={"sendToLedger": "true"}, json_body={
                "date": "2026-02-15",
                "description": desc,
                "postings": [
                    {"row": 1, "account": {"id": acct_id}, "amountGross": amount, "amountGrossCurrency": amount},
                    {"row": 2, "account": {"id": bank_id}, "amountGross": -amount, "amountGrossCurrency": -amount},
                ],
            })
            print(f"  Feb voucher: {desc} = {amount}")

        # Save account names for check() to verify project naming
        expected["account_names"] = dict(self._account_names)

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        top_accounts = expected.get("top_3_accounts", [7100, 6500, 6300])
        account_names = expected.get("account_names", {})

        # Check 1: At least 3 new internal projects created (2pts)
        resp = verifier.get("/project", {
            "isInternal": "true",
            "fields": "id,name,isInternal,projectActivities(*)",
            "count": 50, "sorting": "-id",
        })
        projects = resp.get("values", [])
        recent_internal = [p for p in projects[:10] if p.get("isInternal")]

        checks.append(Check(
            name="Internal projects created",
            passed=len(recent_internal) >= 3,
            expected="at least 3 internal projects",
            actual=f"{len(recent_internal)} recent internal projects found",
            points=2,
        ))

        # Check 2: Projects reference correct accounts (3pts)
        # Build match terms from account names and numbers
        match_terms = []
        for acct_num in top_accounts:
            match_terms.append(str(acct_num))
            name = account_names.get(acct_num, "")
            if name:
                # Add full name and first word (e.g., "Bilgodtgjørelse" from "Bilgodtgjørelse oppgavepliktig")
                match_terms.append(name.lower())
                first_word = name.split()[0].lower() if name else ""
                if len(first_word) > 3:
                    match_terms.append(first_word)

        matches = 0
        for proj in recent_internal[:6]:
            proj_name = (proj.get("name") or "").lower()
            for term in match_terms:
                if term and term in proj_name:
                    matches += 1
                    break

        checks.append(Check(
            name="Projects reference correct accounts",
            passed=matches >= 2,
            expected=f"at least 2 projects named after top accounts ({top_accounts})",
            actual=f"{matches} projects match account names/numbers",
            points=3,
        ))

        # Check 3: Projects have unique custom activities (3pts)
        projects_with_custom_activity = 0
        for proj in recent_internal[:6]:
            activities = proj.get("projectActivities", [])
            for pa in activities:
                act_id = pa.get("activity", {}).get("id")
                if not act_id:
                    continue
                act_detail = verifier.get(f"/activity/{act_id}", {"fields": "id,name"})
                act_name = act_detail.get("value", {}).get("name", "")
                if act_name and "prosjektadministrasjon" not in act_name.lower() and "fakturerbart" not in act_name.lower():
                    projects_with_custom_activity += 1
                    break

        checks.append(Check(
            name="Projects have unique custom activities",
            passed=projects_with_custom_activity >= 2,
            expected="at least 2 projects with custom (non-default) activities",
            actual=f"{projects_with_custom_activity} have custom activity",
            points=3,
        ))

        # Check 4: New activities were created (2pts)
        resp_act = verifier.get("/activity", {
            "isProjectActivity": "true",
            "fields": "id,name,activityType",
            "count": 50, "sorting": "-id",
        })
        all_activities = resp_act.get("values", [])
        custom_activities = [a for a in all_activities[:10]
                           if a.get("name", "") not in ("Prosjektadministrasjon", "Fakturerbart arbeid")
                           and "PROJECT" in a.get("activityType", "")]

        checks.append(Check(
            name="Custom activities created",
            passed=len(custom_activities) >= 2,
            expected="at least 2 new custom activities",
            actual=f"{len(custom_activities)} custom activities found",
            points=2,
        ))

        return checks
