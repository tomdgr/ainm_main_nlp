"""Task 22: Post expense from PDF receipt to correct account and department."""

import base64
import io
import re

import pymupdf

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


def _generate_receipt_pdf(
    store_name: str,
    store_address: str,
    store_org: str,
    date: str,
    items: list[tuple[str, float]],
    vat_rate: float = 0.25,
    payment_method: str = "Bedriftskort",
) -> bytes:
    """Generate a simple receipt PDF matching competition format."""
    total = sum(price for _, price in items)
    vat_amount = total * vat_rate / (1 + vat_rate)

    doc = pymupdf.open()
    page = doc.new_page(width=350, height=500)

    # Header
    y = 30
    page.insert_text((140, y), store_name, fontsize=14, fontname="helv")
    y += 18
    page.insert_text((80, y), store_address, fontsize=8, fontname="helv")
    y += 14
    page.insert_text((120, y), f"Org.nr: {store_org}", fontsize=8, fontname="helv")
    y += 20
    page.insert_text((90, y), f"KVITTERING - {date}", fontsize=10, fontname="helv")

    # Column headers
    y += 25
    page.insert_text((30, y), "Vare", fontsize=9, fontname="helv")
    page.insert_text((250, y), "Pris", fontsize=9, fontname="helv")

    # Items
    for item_name, price in items:
        y += 16
        page.insert_text((30, y), item_name, fontsize=9, fontname="helv")
        page.insert_text((250, y), f"{price:.2f} kr", fontsize=9, fontname="helv")

    # Totals
    y += 25
    page.draw_line((30, y - 5), (320, y - 5))
    page.insert_text((30, y), "Totalt:", fontsize=10, fontname="helv")
    page.insert_text((250, y), f"{total:.2f} kr", fontsize=10, fontname="helv")
    y += 16
    page.insert_text((40, y), f"  herav MVA {int(vat_rate * 100)}%:", fontsize=8, fontname="helv")
    page.insert_text((250, y), f"{vat_amount:.2f} kr", fontsize=8, fontname="helv")

    # Payment
    y += 20
    page.insert_text((30, y), f"Betalt med: {payment_method}", fontsize=9, fontname="helv")

    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


# Receipt templates — each defines a scenario the agent must handle
_RECEIPTS = [
    {
        "store_name": "Clas Ohlson",
        "store_address": "Karl Johans gate 12, 0154 Oslo",
        "store_org": "987654321",
        "date": "15.03.2026",
        "items": [("Kontorrekvisita", 4500.0)],
        "target_item": "Kontorrekvisita",
        "target_amount": 4500.0,
        "department": "Økonomi",
        "prompt_template": (
            'Vi har mottatt denne kvitteringen. Bokfør utgiften for {item} '
            'til avdeling {dept}. Bruk riktig utgiftskonto og sørg for korrekt MVA-behandling.'
        ),
    },
    {
        "store_name": "Circle K",
        "store_address": "Storgata 50, 3015 Drammen",
        "store_org": "912345678",
        "date": "22.04.2026",
        "items": [("Drivstoff", 1850.0)],
        "target_item": "Drivstoff",
        "target_amount": 1850.0,
        "department": "Logistikk",
        "prompt_template": (
            'Registrer utgiften fra denne kvitteringen for {item} '
            'i avdeling {dept}. Bruk korrekt konto og MVA-behandling.'
        ),
    },
]


class ReceiptExpenseTask(BaseTask):
    """Tier 2 task: Post expense from a PDF receipt to the correct account and department.

    This simulates task_22 from the competition. The agent receives a PDF receipt
    and must extract the item, amount, and VAT, then post a voucher to the correct
    expense account with the correct department.
    """

    name = "Expense from PDF Receipt"
    tier = 2
    optimal_calls = 5  # dept + account + vatType + bank account setup + POST voucher

    def __init__(self, task_id: str):
        super().__init__(task_id)
        self._current_receipt: dict | None = None

    @property
    def prompts(self) -> list[str]:
        return [
            r["prompt_template"].format(item=r["target_item"], dept=r["department"])
            for r in _RECEIPTS
        ]

    def _pick_receipt(self, prompt: str) -> dict:
        """Match prompt to receipt template."""
        for r in _RECEIPTS:
            expected_prompt = r["prompt_template"].format(
                item=r["target_item"], dept=r["department"]
            )
            if prompt == expected_prompt:
                return r
        # Default to first
        return _RECEIPTS[0]

    def extract_expected(self, prompt: str) -> dict:
        receipt = self._pick_receipt(prompt)
        self._current_receipt = receipt
        return {
            "target_item": receipt["target_item"],
            "target_amount": receipt["target_amount"],
            "department": receipt["department"],
            "date": receipt["date"],
        }

    def get_files(self, expected: dict) -> list[dict]:
        """Generate and return a PDF receipt."""
        receipt = self._current_receipt or _RECEIPTS[0]
        pdf_bytes = _generate_receipt_pdf(
            store_name=receipt["store_name"],
            store_address=receipt["store_address"],
            store_org=receipt["store_org"],
            date=receipt["date"],
            items=receipt["items"],
        )
        return [{
            "filename": "kvittering.pdf",
            "content_base64": base64.b64encode(pdf_bytes).decode(),
            "mime_type": "application/pdf",
        }]

    def setup(self, base_url: str, session_token: str, expected: dict):
        """Ensure department exists."""
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
            self._api(base_url, session_token, "POST", "/department", json_body={
                "name": dept_name,
            })
            print(f"  Created department '{dept_name}'")

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        amount = expected.get("target_amount", 0)
        dept_name = expected.get("department", "")

        # Find recent vouchers — search from receipt date onwards, sorted by newest
        date = expected.get("date", "01.01.2026")
        parts = date.split(".")
        date_from = f"{parts[2]}-{parts[1]}-{parts[0]}" if len(parts) == 3 else "2026-01-01"

        resp = verifier.get("/ledger/voucher", {
            "dateFrom": date_from, "dateTo": "2099-12-31",
            "count": 20, "sorting": "-id",
        })
        vouchers = resp.get("values", [])

        matching_posting = None
        matching_acct_number = None
        for v in vouchers[:10]:
            v_detail = verifier.get(f"/ledger/voucher/{v['id']}", {
                "fields": "id,description,postings(*)",
            })
            for p in v_detail.get("value", {}).get("postings", []):
                p_acct_id = p.get("account", {}).get("id")
                p_amount = p.get("amountGross") or 0
                if p_amount <= 0:
                    continue
                # Look up account number to check it's an expense account (5000-7999)
                acct_resp = verifier.get(f"/ledger/account/{p_acct_id}", {"fields": "id,number,name"})
                acct_num = acct_resp.get("value", {}).get("number", 0)
                if 5000 <= acct_num <= 7999 and abs(p_amount - amount) < 200:
                    matching_posting = p
                    matching_acct_number = acct_num
                    break
            if matching_posting:
                break

        # Check 1: Posting exists on an expense account
        checks.append(Check(
            name="Expense posting found",
            passed=matching_posting is not None,
            expected=f"posting on expense account (5000-7999) for ~{amount}",
            actual=f"account {matching_acct_number}" if matching_posting else "NOT FOUND",
            points=3,
        ))

        if not matching_posting:
            return checks

        # Check 2: Amount is approximately correct
        actual_amount = matching_posting.get("amountGross", 0)
        checks.append(Check(
            name="Amount correct",
            passed=abs(actual_amount - amount) < 200,
            expected=str(amount),
            actual=str(actual_amount),
            points=2,
        ))

        # Check 3: Department is set correctly
        posting_dept = matching_posting.get("department")
        if posting_dept and posting_dept.get("id"):
            dept_resp = verifier.get(f"/department/{posting_dept['id']}", {"fields": "id,name"})
            actual_dept = dept_resp.get("value", {}).get("name", "")
            checks.append(Check(
                name="Department correct",
                passed=actual_dept.lower() == dept_name.lower(),
                expected=dept_name,
                actual=actual_dept or "NONE",
                points=2,
            ))
        else:
            checks.append(Check(
                name="Department linked",
                passed=False,
                expected=dept_name,
                actual="No department on posting",
                points=2,
            ))

        return checks
